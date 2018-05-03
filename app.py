import os
import functools

import psycopg2
import requests
from dotenv import load_dotenv
from authlib.flask.client import OAuth
from github import Github
from flask import Flask, render_template, session, redirect, url_for, request
from six.moves.urllib.parse import urlencode

load_dotenv()

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ['SECRET_KEY']

oauth = OAuth(app)

auth0 = oauth.register(
    'auth0',
    client_id=os.environ['AUTH0_CLIENT_ID'],
    client_secret=os.environ['AUTH0_CLIENT_SECRET'],
    api_base_url='https://codeguild.eu.auth0.com',
    access_token_url='https://codeguild.eu.auth0.com/oauth/token',
    authorize_url='https://codeguild.eu.auth0.com/authorize',
    client_kwargs={
        'scope': 'openid profile',
    },
)


@app.before_request
def before_request():
    request.db = psycopg2.connect(dsn=os.environ['DATABASE_URL'])


@app.after_request
def after_request(response):
    request.db.close()
    return response


@app.route('/')
def index():
    return render_template('index.html', repos=get_repos())


@app.route('/p/<name>/', methods=['GET', 'POST'])
def project(name):
    repo = get_repo(name)
    is_editor = can_edit(repo)
    if request.method == 'POST' and is_editor:
        summary = request.form['summary']
        upsert_project(name, summary)
        return redirect(url_for('project', name=name))
    with request.db.cursor() as curs:
        try:
            curs.execute('SELECT id, summary FROM project WHERE name=%s', (name, ))
            project_id, summary = curs.fetchone()
            repo['summary'] = summary
            posts = get_posts(project_id)
        except TypeError:
            repo['summary'] = ''
            posts = []
    return render_template('project.html', repo=repo, is_editor=is_editor, posts=posts)


@app.route('/p/<project_name>/blog/', methods=['POST'])
def create_blog_post(project_name):
    repo = get_repo(project_name)
    is_editor = can_edit(repo)
    if is_editor:
        with request.db.cursor() as curs:
            curs.execute('SELECT id FROM project WHERE name=%s', (project_name, ))
            project_id, = curs.fetchone()
            curs.execute(
                'INSERT INTO blog (project_id, name, body) VALUES (%s, %s, %s)',
                (project_id, request.form['name'], request.form['body']))
            request.db.commit()
    return redirect(url_for('project', name=project_name))


@app.route('/p/<project_name>/blog/<id>/')
def delete_blog_post(project_name, id):
    with request.db.cursor() as curs:
        curs.execute('DELETE FROM blog WHERE id = %s', (id, ))
        request.db.commit()
    return redirect(url_for('project', name=project_name))


@app.route('/signin/')
def signin():
    return auth0.authorize_redirect(
        redirect_uri=url_for('signin_callback', _external=True),
        audience='https://codeguild.eu.auth0.com/userinfo')


@app.route('/signin/callback/')
def signin_callback():
    resp = auth0.authorize_access_token()
    url = 'https://codeguild.eu.auth0.com/userinfo'
    headers = {'authorization': 'Bearer ' + resp['access_token']}
    resp = requests.get(url, headers=headers)
    userinfo = resp.json()
    session['jwt_payload'] = userinfo
    session['profile'] = {
        'user_id': userinfo['sub'],
        'name': userinfo['name'],
        'picture': userinfo['picture']
    }
    return redirect(url_for('index'))


@app.route('/signout/')
def signout():
    session.clear()
    params = {
        'returnTo': url_for('index', _external=True),
        'client_id': os.environ['AUTH0_CLIENT_ID']}
    return redirect(auth0.api_base_url + '/v2/logout?' + urlencode(params))


@functools.lru_cache()
def get_repos():
    g = Github()
    org = g.get_organization('CodeGuild-co')
    repos = org.get_repos()
    repos = sorted(repos, key=lambda r: r.updated_at, reverse=True)
    return [
        {'name': r.name, 'website': r.homepage, 'github': r.html_url, 'description': r.description}
        for r in repos]


@functools.lru_cache()
def get_repo(name):
    g = Github()
    org = g.get_organization('CodeGuild-co')
    repo = org.get_repo(name)
    contributors = repo.get_contributors()
    return {
        'name': repo.name,
        'description': repo.description,
        'website': repo.homepage,
        'github': repo.html_url,
        'contributors': [
            {'name': c.login, 'link': c.html_url, 'id': c.id}
            for c in contributors]
    }


def can_edit(repo):
    try:
        user_id = int(session['profile']['user_id'].replace('github|', ''))
        return user_id in [c['id'] for c in repo['contributors']]
    except (KeyError, ValueError):
        pass
    return False


def upsert_project(name, summary):
    with request.db.cursor() as curs:
        try:
            curs.execute('INSERT INTO project (name, summary) VALUES (%s, %s)', (name, summary))
        except psycopg2.IntegrityError:
            request.db.rollback()
            curs.execute('UPDATE project SET summary=%s WHERE name=%s', (summary, name))
        finally:
            request.db.commit()


def get_posts(project_id):
    with request.db.cursor() as curs:
        curs.execute('SELECT name, created_on, body, id FROM blog WHERE project_id=%s', (project_id, ))
        posts = curs.fetchall()
        return [
            {'name': p[0], 'created_on': p[1], 'body': p[2], 'id':p[3]}
            for p in posts]


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
