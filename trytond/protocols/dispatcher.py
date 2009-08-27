# -*- coding: utf-8 -*-
#This file is part of Tryton.  The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.
from trytond.pool import Pool
from trytond import security
from trytond.tools import Cache, find_language_context
from trytond.backend import Database, DatabaseIntegrityError
from trytond.config import CONFIG
from trytond.version import VERSION
from trytond.monitor import monitor
import traceback
import logging
import time
import sys
try:
    import hashlib
except ImportError:
    hashlib = None
    import sha
import base64
import pydoc

def dispatch(host, port, protocol, database_name, user, session, object_type,
        object_name, method, *args, **kargs):

    if CONFIG['auto_reload'] and monitor():
        Pool.start()

    if object_type == 'common':
        if method == 'login':
            res = security.login(database_name, user, session)
            Cache.clean(database_name)
            logger = logging.getLogger('dispatcher')
            msg = res and 'successful login' or 'bad login or password'
            logger.info('%s \'%s\' from %s:%d using %s on database \'%s\'' % \
                    (msg, user, host, port, protocol, database_name))
            Cache.resets(database_name)
            return res or False
        elif method == 'logout':
            name = security.logout(database_name, user, session)
            logger = logging.getLogger('dispatcher')
            logger.info('logout \'%s\' from %s:%d using %s on database \'%s\'' % \
                    (name, host, port, protocol, database_name))
            return True
        elif method == 'version':
            return VERSION
        elif method == 'timezone_get':
            return time.tzname[0]
        elif method == 'list_lang':
            return [
                ('cs_CZ', 'Čeština'),
                ('de_DE', 'Deutsch'),
                ('en_US', 'English'),
                ('es_ES', 'Español (España)'),
                ('es_CO', 'Español (Colombia)'),
                ('fr_FR', 'Français'),
            ]
        elif method == 'db_exist':
            try:
                database = Database(*args, **kargs).connect()
                cursor = database.cursor()
                cursor.close(close=True)
                return True
            except:
                return False
        elif method == 'list':
            database = Database().connect()
            try:
                cursor = database.cursor()
                res = database.list(cursor)
                cursor.close(close=True)
            except:
                res = []
            return res
        elif method == 'create':
            return create(*args, **kargs)
        elif method == 'restore':
            return restore(*args, **kargs)
        elif method == 'drop':
            return drop(*args, **kargs)
        elif method == 'dump':
            return dump(*args, **kargs)
        return
    elif object_type == 'system':
        database = Database(database_name).connect()
        database_list = Pool.database_list()
        pool = Pool(database_name)
        if not database_name in database_list:
            pool.init()
        if method == 'listMethods':
            res = []
            for type in ('model', 'wizard', 'report'):
                for object_name, obj in pool.iterobject(type=type):
                    for method in obj._rpc:
                        res.append(type + '.' + object_name + '.' + method)
            return res
        elif method == 'methodSignature':
            return 'signatures not supported'
        elif method == 'methodHelp':
            res = []
            args_list = args[0].split('.')
            object_type = args_list[0]
            object_name = '.'.join(args_list[1:-1])
            method = args_list[-1]
            obj = pool.get(object_name, type=object_type)
            return pydoc.getdoc(getattr(obj, method))

    user = security.check(database_name, user, session)

    database = Database(database_name).connect()
    cursor = database.cursor()
    try:
        try:
            Cache.clean(database_name)
            database_list = Pool.database_list()
            pool = Pool(database_name)
            if not database_name in database_list:
                pool.init()
            obj = pool.get(object_name, type=object_type)
            if method not in obj._rpc:
                raise Exception('UserError', 'Calling method %s on ' \
                        '%s %s is not allowed!' % \
                        (method, object_type, object_name))

            res = getattr(obj, method)(cursor, user, *args, **kargs)
            if obj._rpc[method]:
                cursor.commit()
        except Exception, exception:
            if CONFIG['verbose'] or (exception.args \
                    and str(exception.args[0]) not in \
                    ('NotLogged', 'ConcurrencyException', 'UserError',
                        'UserWarning')):
                tb_s = reduce(lambda x, y: x + y,
                        traceback.format_exception(*sys.exc_info()))
                logger = logging.getLogger('dispatcher')
                logger.error('Exception calling method %s on ' \
                        '%s %s from %s@%s:%d/%s:\n' % \
                        (method, object_type, object_name, user, host, port,
                            database_name) + tb_s.decode('utf-8', 'ignore'))
            cursor.rollback()
            raise
    finally:
        if not (object_name == 'res.request' and method == 'request_get'):
            user.reset_timestamp()
        cursor.close()
        Cache.resets(database_name)
    return res

def create(database_name, password, lang, admin_password):
    '''
    Create a database

    :param database_name: the database name
    :param password: the server password
    :param lang: the default language for the database
    :param admin_password: the admin password
    :return: True if succeed
    '''
    security.check_super(password)
    res = False
    logger = logging.getLogger('database')

    database = Database().connect()
    cursor = database.cursor(autocommit=True)
    try:
        try:
            database.create(cursor, database_name)
            cursor.commit()
            cursor.close(close=True)

            database = Database(database_name).connect()
            cursor = database.cursor()
            database.init(cursor)
            cursor.commit()
            cursor.close()

            cursor = None
            database = None

            pool = Pool(database_name)
            pool.init(update=True, lang=[lang])
            database = Database(database_name).connect()
            cursor = database.cursor()

            #XXX replace with model write
            if lang != 'en_US':
                cursor.execute('UPDATE ir_lang ' \
                        'SET translatable = True ' \
                        'WHERE code = %s', (lang,))
            cursor.execute('UPDATE res_user ' \
                    'SET language = ' \
                        '(SELECT id FROM ir_lang WHERE code = %s LIMIT 1) '\
                    'WHERE login <> \'root\'', (lang,))
            if hashlib:
                admin_password = hashlib.sha1(admin_password).hexdigest()
            else:
                admin_password = sha.new(admin_password).hexdigest()
            cursor.execute('UPDATE res_user ' \
                    'SET password = %s ' \
                    'WHERE login = \'admin\'', (admin_password,))
            module_obj = pool.get('ir.module.module')
            if module_obj:
                module_obj.update_list(cursor, 0)
            cursor.commit()
            res = True
        except:
            logger.error('CREATE DB: %s failed' % (database_name,))
            import traceback, sys
            tb_s = reduce(lambda x, y: x+y,
                    traceback.format_exception(*sys.exc_info()))
            logger.error('Exception in call: \n' + tb_s)
            raise
        else:
            logger.info('CREATE DB: %s' % (database_name,))
    finally:
        if cursor:
            cursor.close()
    return res

def drop(database_name, password):
    security.check_super(password)
    Database(database_name).close()
    # Sleep to let connections close
    time.sleep(1)
    logger = logging.getLogger('database')

    database = Database().connect()
    cursor = database.cursor(autocommit=True)
    try:
        try:
            database.drop(cursor, database_name)
            cursor.commit()
        except:
            logger.error('DROP DB: %s failed' % (database_name,))
            import traceback, sys
            tb_s = reduce(lambda x, y: x+y,
                    traceback.format_exception(*sys.exc_info()))
            logger.error('Exception in call: \n' + tb_s)
            raise
        else:
            logger.info('DROP DB: %s' % (database_name))
            Pool.stop(database_name)
    finally:
        cursor.close(close=True)
    return True

def dump(database_name, password):
    security.check_super(password)
    Database(database_name).close()
    # Sleep to let connections close
    time.sleep(1)
    logger = logging.getLogger('database')

    data = Database.dump(database_name)
    logger.info('DUMP DB: %s' % (database_name))
    return base64.encodestring(data)

def restore(database_name, password, data):
    logger = logging.getLogger('database')
    security.check_super(password)
    try:
        database = Database().connect()
        cursor = database.cursor()
        cursor.close(close=True)
        raise Exception("Database already exists!")
    except:
        pass
    data = base64.decodestring(data)
    Database.restore(database_name, data)
    logger.info('RESTORE DB: %s' % (database_name))
    return True