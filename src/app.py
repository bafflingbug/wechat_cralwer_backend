#!/usr/bin/env python
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, render_template
from collections import deque
import json
import MySQLdb
import os
import logging
import requests
import re
from multiprocessing import Queue, get_logger, Process
from src import config

app = Flask(__name__)

default_header = {
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.140 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'zh-CN,zh;q=0.9'
}


def initLogger(name, subname='default', workspace='.', multiproc=False, stream=True):
    if not os.path.exists(workspace):
        os.makedirs(workspace)

    log = None
    if multiproc:
        log = get_logger()
    else:
        log = logging.getLogger(name)

    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(r'[%(levelname)s][%(asctime)s %(filename)s:%(lineno)d] %(message)s')

    if stream:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(config.logger.stream_level)
        log.addHandler(sh)
    else:
        sh = None

    fh = logging.FileHandler('%s/%s.log' % (workspace, subname))
    fh.setFormatter(fmt)
    fh.setLevel(config.logger.file_level)
    log.addHandler(fh)

    return log, sh, fh


log, _, _ = initLogger('wechat-spider',
                       subname='default' if config.logger.name is None else config.logger.name,
                       workspace='.' if config.logger.path is None else config.logger.path,
                       multiproc=True,
                       stream=config.logger.stream)


class DBS:
    def __init__(self, user, password, host='localhost', port=3306):
        if re.match('(^(25[0-5]|2[0-4]\d|[0-1]?\d?\d)(\.(25[0-5]|2[0-4]\d|[0-1]?\d?\d)){3}$)|(^localhost$)',
                    host) is None:
            raise Exception('host error')
        if not 0 < port <= 65535:
            raise Exception('port error')
        if type(user) is not str or type(password) is not str:
            raise Exception('args type error')
        self.__host = host
        self.__port = port
        self.__user = user
        self.__password = password
        self.__cur = None
        self.__conn = None

    def connect(self, database):
        try:
            self.__conn = MySQLdb.connect(db=database, host=self.__host, port=self.__port, user=self.__user,
                                          passwd=self.__password)
            self.__cur = self.__conn.cursor()
            log.info('[DBS.connect]Successfully connected to database: server->%s:%d', self.__host, self.__port)
            return True
        except Exception:
            log.error('[DBS.connect]Unable to connect to the database server!', exc_info=True)
            return False

    def close(self):
        try:
            if self.__cur is not None:
                self.__cur.close()
        except Exception:
            log.error('[DBS.close]Cannot connect to close the database server!', exc_info=True)
        finally:
            self.__cur = None

    def execute(self, sql):
        if type(sql) is not str:
            return None
        if self.__cur is not None:
            try:
                self.__cur.execute(sql)
                self.__conn.commit()
                return self.__cur.fetchall()
            except Exception:
                self.__conn.rollback()
                log.error('[DBS.execute]SQL Error: msg->%s', sql, exc_info=True)
                return None

    def __del__(self):
        try:
            self.__cur.close()
        except Exception:
            pass
        finally:
            del self.__cur


def http_get(url):
    resp = requests.get(url, headers=default_header)
    html = resp.text
    raw = resp.content
    return html, raw


class UrlList:
    __LIST = [['http://mp.weixin.qq.com/mp/getmasssendmsg?__biz=%s#wechat_webview_type=1&wechat_redirect' % biz, 'html']
              for biz in config.biz_list]
    __restart = False if config.auto_restart is None else config.auto_restart

    def __init__(self):
        self.__que = deque(self.__LIST)

    def __len__(self):
        return len(self.__que)

    def add(self, url):
        try:
            self.__que.append(url)
            return True
        except Exception:
            # TODO log
            return False

    def get(self):
        if len(self.__que) == 0:
            if self.__restart:
                self.restart()
            else:
                return None
        return self.__que.pop()

    def restart(self):
        self.__que.extend(self.__LIST)


@app.route('/api/post', methods=['POST'])
def post():
    data = request.data
    log.info('[psot]get data->%s' % data)
    try:
        j_d = json.loads(data)
        log.debug('[post]json data->%s' % str(j_d))
        if j_d['can_msg_continue'] and 1 == int(j_d['can_msg_continue']):
            nu = 'https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=%s&f=json&offset=%s&count=10&is_ok=1&scene=124&uin=777&key=777&wxtoken=&appmsg_token=%s&f=json'
            nu = nu % (j_d['__biz'], j_d['next_offset'], j_d['appmsg_token'])
            log.info('[post]find next url->%s' % nu)
            b = url_list.add([nu, 'json'])  # TODO page0 url
            if not b:
                log.error('[psot]add url_list error')
        print(j_d['data'])
        if j_d['type'] and 1 == int(j_d['type']):
            wechat_home_interpreter(j_d['data'])
        else:
            log.warning('[post]type error data->%s' % data)
    except Exception:
        log.error('[post]error', exc_info=True)
    finally:
        return 'ok'


@app.route('/api/next')
def next_url():
    nu = url_list.get()
    if nu is None:
        url_list.restart()
        return jsonify({'type': 'none'}), 200, {'Access-Control-Allow-Origin': '*'}
    elif nu[0].find('getmasssendmsg') > 0 or nu[0].find('action=getmsg'):
        log.info('[next_url]return url->%s' % nu[0])
        return jsonify({'type': nu[1], 'url': nu[0]}), 200, {'Access-Control-Allow-Origin': '*'}
    else:
        log.warning('[next_url]WTF url->%s' % nu[0]), 200, {'Access-Control-Allow-Origin': '*'}
        return jsonify({'type': 'none'}), 200, {'Access-Control-Allow-Origin': '*'}


sleep_time = config.sleep_time if type(config.sleep_time) == int and config.sleep_time < pow(2, 31) else 5000
sleep_time_none = config.sleep_time_none if type(config.sleep_time_none) == int \
                                            and config.sleep_time_none < pow(2, 31) else 60000 * 60 * 2


@app.route('/api/html')
def html():
    return render_template('start.html', url=config.server_url, time=sleep_time)


@app.route('/api/js')
def js():
    return render_template('js.html', url=config.server_url, time=sleep_time)


@app.route('/api/sleep')
def sleep_html():
    return render_template('sleep.html', url=config.server_url, time=sleep_time_none)


def wechat_home_interpreter(data):
    try:
        data = json.loads(data)
        for msg in data['list']:
            if msg['comm_msg_info']['type'] == 49:
                url = msg['app_msg_ext_info']['content_url']
                url = url.replace('\/', '/')
                w2wque.put(url)
                log.info('[wechat_home_interpreter]msg ->%s' % url)
                for m in msg['app_msg_ext_info']['multi_app_msg_item_list']:
                    url = m['content_url']
                    url = url.replace('\/', '/')
                    w2wque.put(url)
                    log.info('[wechat_home_interpreter]msg ->%s' % url)
    except Exception:
        log.error('[wechat_home_interpreter]error', exc_info=True)


def wechat_msg_interpreter():
    from lxml import etree
    from datetime import datetime
    import hashlib
    dbs = DBS(user=config.db_server.user, password=config.db_server.password, host=config.db_server.host,
              port=config.db_server.port)
    while True:
        url = w2wque.get()
        if not url or url == '':
            continue
        try:
            html, msg_html = http_get(url)
            selector = etree.HTML(html)
            msg_title = selector.xpath('//*[@id=\'activity-name\']/text()')[0].replace('\r', '').replace('\n', '') \
                .replace(' ', '')
            msg_date = selector.xpath('//*[@id=\'post-date\']/text()')[0]
            msg_author = selector.xpath('//*[@id=\'post-user\']/text()')[0]
            wechat_msg_text = selector.xpath('//div[@id=\'js_content\']//*/text()')
            msg_text = ''
            for text in wechat_msg_text:
                msg_text += text
            md5 = hashlib.md5()
            md5.update(msg_title)
            msg_title_md5 = md5.hexdigest()
            data = {'title': str(msg_title), 'date': str(msg_date), 'author': str(msg_author), 'text': str(msg_text),
                    'html': msg_html, 'url': str(url), 'title_md5': msg_title_md5}
            dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            dbs.connect('wechat_msg')
            sql = "REPLACE INTO msg(url,title,title_md5,publish_time,content,author,create_time,update_time)VALUES('%s','%s','%s','%s','%s','%s','%s','%s')"
            d = dbs.execute(
                sql % (
                    data['url'], data['title'], data['title_md5'], data['date'], data['text'], data['author'], dt, dt))
            if d is not None:
                log.info('[wechat_msg_interpreter]Successful add to db')
            else:
                log.error('[wechat_msg_interpreter]can not add to db data->%s' % data)
            dbs.close()
        except ImportError:
            log.error('[wechat_msg_interpreter]error', exc_info=True)


msg_p = Process(name='msg', target=wechat_msg_interpreter)
url_list = UrlList()
w2wque = Queue()

if __name__ == '__main__':
    import sys

    reload(sys)
    sys.setdefaultencoding('utf8')
    try:
        with app.app_context():
            msg_p.start()
            app.run(host='0.0.0.0', debug=True)
            msg_p.join()
    except Exception:
        log.error('[__main__]error', exc_info=True)
