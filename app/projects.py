"""Projects, focus groups and source-backed context, stored alongside messages."""
from __future__ import annotations

import json

from . import db


class Conflict(ValueError):
    """The request is valid but conflicts with stored state."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_admins (
    agent_id INTEGER PRIMARY KEY REFERENCES agents(id), granted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL, purpose TEXT NOT NULL,
    board_id INTEGER NOT NULL UNIQUE REFERENCES boards(id),
    owner_id INTEGER NOT NULL REFERENCES agents(id),
    repositories TEXT NOT NULL DEFAULT '[]',
    operators TEXT NOT NULL DEFAULT '[]',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_groups (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    slug TEXT NOT NULL COLLATE NOCASE,
    name TEXT NOT NULL, purpose TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '', related_groups TEXT NOT NULL DEFAULT '[]',
    board_id INTEGER NOT NULL UNIQUE REFERENCES boards(id),
    archived INTEGER NOT NULL DEFAULT 0,
    seed_thread_id INTEGER REFERENCES messages(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(project_id, slug)
);
CREATE TABLE IF NOT EXISTS context_revisions (
    id INTEGER PRIMARY KEY,
    board_id INTEGER NOT NULL REFERENCES boards(id),
    base_revision INTEGER NOT NULL, revision INTEGER,
    status TEXT NOT NULL,
    body TEXT NOT NULL, sources TEXT NOT NULL, reason TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES agents(id),
    approved_by INTEGER REFERENCES agents(id),
    created_at TEXT NOT NULL, published_at TEXT,
    UNIQUE(board_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_context_board ON context_revisions(board_id, id);
CREATE TABLE IF NOT EXISTS project_audit (
    id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id),
    actor_id INTEGER NOT NULL REFERENCES agents(id),
    action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_pools (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id),
    configured INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS project_pool_bots (
    agent_id INTEGER PRIMARY KEY REFERENCES agents(id),
    project_id INTEGER NOT NULL REFERENCES projects(id)
);
"""


def init():
    with db._write_lock:
        db.conn().executescript(SCHEMA)


def is_admin(agent):
    return bool(agent and agent['kind'] == 'human' and db.conn().execute(
        'SELECT 1 FROM project_admins WHERE agent_id=?', (agent['id'],)).fetchone())


def grant_admin(agent):
    if agent['kind'] != 'human':
        raise PermissionError('Project management requires a human identity.')
    with db._write_lock, db.conn() as c:
        c.execute('INSERT OR IGNORE INTO project_admins VALUES (?,?)', (agent['id'], db.now()))


def can_manage(project, agent):
    return bool(agent and agent['kind'] == 'human' and (
        is_admin(agent) or project['owner_id'] == agent['id'] or
        agent['id'] in json.loads(project['operators'])))


def require_manage(project, agent):
    if not can_manage(project, agent):
        raise PermissionError('Only authorized human project operators can manage this project.')


def find_project(slug):
    row = db.conn().execute('SELECT * FROM projects WHERE slug=?', (slug,)).fetchone()
    if row is None:
        raise KeyError('No such project.')
    return dict(row)


def find_group(project, slug):
    row = db.conn().execute('SELECT * FROM project_groups WHERE project_id=? AND slug=?',
                            (project['id'], slug)).fetchone()
    if row is None:
        raise KeyError('No such group in this project.')
    return dict(row)


def context(board_id):
    row = db.conn().execute(
        "SELECT r.*, a.handle author, ap.handle approved_by_handle FROM context_revisions r"
        " JOIN agents a ON a.id=r.author_id LEFT JOIN agents ap ON ap.id=r.approved_by"
        " WHERE board_id=? AND status='published' ORDER BY revision DESC LIMIT 1", (board_id,)).fetchone()
    if row is None:
        return {'revision': 0, 'body': '', 'sources': [], 'author': None, 'created_at': None}
    return {'revision': row['revision'], 'body': row['body'], 'sources': json.loads(row['sources']),
            'reason': row['reason'], 'author': row['author'], 'created_at': row['published_at'],
            'approved_by': row['approved_by_handle']}


def readiness(board_id):
    if context(board_id)['revision']:
        return 'ready'
    if db.conn().execute("SELECT 1 FROM context_revisions WHERE board_id=? AND status='draft' LIMIT 1",
                         (board_id,)).fetchone():
        return 'draft'
    return 'unseeded'


def describe_group(project, group, agent=None, full=True):
    out = {k: group[k] for k in ('id', 'slug', 'name', 'purpose', 'scope', 'created_at', 'updated_at', 'seed_thread_id')}
    out.update(channel=db.get_board_by_id(group['board_id'])['slug'],
               archived=bool(group['archived'] or project['archived']),
               related_groups=json.loads(group['related_groups']),
               readiness=readiness(group['board_id']), can_manage=can_manage(project, agent))
    if full:
        out['context'] = context(group['board_id'])
    else:
        out['purpose'] = out['purpose'][:160]
        out['scope'] = out['scope'][:160]
    return out


def describe_project(project, agent=None, full=True):
    out = {k: project[k] for k in ('id', 'slug', 'name', 'purpose', 'created_at', 'updated_at')}
    out.update(channel=db.get_board_by_id(project['board_id'])['slug'],
               archived=bool(project['archived']), readiness=readiness(project['board_id']),
               can_manage=can_manage(project, agent))
    if full:
        out.update(repositories=json.loads(project['repositories']),
                   operators=[db.get_agent_by_id(a)['handle'] for a in json.loads(project['operators'])],
                   context=context(project['board_id']))
        out['bot_pool'] = pool_settings(project)
        groups = db.conn().execute('SELECT * FROM project_groups WHERE project_id=? ORDER BY id',
                                   (project['id'],)).fetchall()
        out['groups'] = [describe_group(project, dict(g), agent, full=False) for g in groups]
    else:
        out['purpose'] = out['purpose'][:160]
    return out


def list_projects(agent=None, after=0, limit=20):
    rows = db.conn().execute('SELECT * FROM projects WHERE id>? ORDER BY id LIMIT ?',
                            (after, limit + 1)).fetchall()
    return {'projects': [describe_project(dict(p), agent, full=False) for p in rows[:limit]],
            'can_create': is_admin(agent), 'has_more': len(rows) > limit,
            'next_after': rows[limit - 1]['id'] if len(rows) > limit else None}


def _audit(c, board_id, agent, action, detail):
    c.execute('INSERT INTO project_audit(board_id,actor_id,action,detail,created_at) VALUES (?,?,?,?,?)',
              (board_id, agent['id'], action, json.dumps(detail), db.now()))


def _event(c, board_id, agent, action, detail):
    # These events participate in message cursors without waking every harness.
    cur = c.execute('INSERT INTO messages(board_id,agent_id,body,meta,created_at) VALUES (?,?,?,?,?)',
                    (board_id, agent['id'], action, json.dumps({'kind': 'context_update', **detail}), db.now()))
    c.execute('UPDATE messages SET thread_id=id WHERE id=?', (cur.lastrowid,))
    return cur.lastrowid


def _channel(c, slug, purpose, agent):
    existing = c.execute('SELECT id FROM boards WHERE slug=?', (slug,)).fetchone()
    if existing:
        raise Conflict('Channel already exists; choose a new slug. Existing channels are not adopted automatically.')
    return c.execute("INSERT INTO boards(slug,topic,kind,created_at,created_by) VALUES (?,?,'channel',?,?)",
                     (slug, purpose, db.now(), agent['id'])).lastrowid


def create_project(data, agent):
    if not is_admin(agent):
        raise PermissionError('Unlock project management with a configured management key first.')
    with db._write_lock, db.conn() as c:
        if data.get('adopt_channel'):
            board = db.get_board(data['slug'])
            if not board or board['kind'] != 'channel':
                raise ValueError('Only an existing public channel can be adopted.')
            if route(data['slug'])['project']:
                raise ValueError('This channel already belongs to a project.')
            board_id = board['id']
        else:
            board_id = _channel(c, data['slug'], data['purpose'], agent)
        c.execute('INSERT INTO projects(slug,name,purpose,board_id,owner_id,repositories,created_at,updated_at)'
                  ' VALUES (?,?,?,?,?,?,?,?)', (data['slug'], data['name'], data['purpose'], board_id,
                  agent['id'], json.dumps(data['repositories']), db.now(), db.now()))
        _audit(c, board_id, agent, 'create_project', data)
        _event(c, board_id, agent, 'Project created.', {'action': 'project_created'})
    return describe_project(find_project(data['slug']), agent)


def create_group(slug, data, agent):
    with db._write_lock, db.conn() as c:
        project = find_project(slug)
        require_manage(project, agent)
        if project['archived']:
            raise ValueError('Archived projects cannot accept new groups.')
        count = c.execute('SELECT COUNT(*) FROM project_groups WHERE project_id=?', (project['id'],)).fetchone()[0]
        if count >= 50:
            raise ValueError('A project supports at most 50 groups.')
        _related(project, data['related_groups'])
        channel = f"{slug}.{data['slug']}"
        if len(channel) > 64:
            raise ValueError('Combined project/group channel slug exceeds 64 characters.')
        board_id = _channel(c, channel, data['purpose'], agent)
        c.execute('INSERT INTO project_groups(project_id,slug,name,purpose,scope,related_groups,board_id,created_at,updated_at)'
                  ' VALUES (?,?,?,?,?,?,?,?,?)', (project['id'], data['slug'], data['name'], data['purpose'],
                  data['scope'], json.dumps(data['related_groups']), board_id, db.now(), db.now()))
        _audit(c, board_id, agent, 'create_group', data)
        _event(c, board_id, agent, 'Group created. Seed its context before starting normal work.',
               {'action': 'group_created'})
    return describe_group(project, find_group(project, data['slug']), agent)


def _related(project, slugs):
    for slug in slugs:
        find_group(project, slug)


def update_scope(slug, group_slug, data, agent):
    with db._write_lock, db.conn() as c:
        project = find_project(slug)
        require_manage(project, agent)
        scope = find_group(project, group_slug) if group_slug else project
        if scope['archived'] or project['archived']:
            raise Conflict('Archived records are read-only; restore workflows are deferred.')
        if data.get('archived'):
            if not data.pop('archive_confirm', False):
                raise ValueError('Confirm active work has finished or been released before archiving.')
            # shortcut: claims are still prose until milestone 3; operators reconcile them manually.
        data.pop('archive_confirm', None)
        if group_slug and 'related_groups' in data:
            if group_slug in data['related_groups']:
                raise ValueError('A group cannot be related to itself.')
            _related(project, data['related_groups'])
        if not group_slug and 'operators' in data:
            ids = []
            for h in data['operators']:
                a = db.get_agent_by_handle(h)
                if not a or a['kind'] != 'human':
                    raise ValueError('Project operators must be existing human identities.')
                ids.append(a['id'])
            data['operators'] = ids
        table = 'project_groups' if group_slug else 'projects'
        for key, value in data.items():
            if key in ('repositories', 'operators', 'related_groups'):
                value = json.dumps(value)
            c.execute(f'UPDATE {table} SET {key}=?, updated_at=? WHERE id=?', (value, db.now(), scope['id']))
        if not group_slug and data.get('archived'):
            c.execute('DELETE FROM project_pool_bots WHERE project_id=?', (project['id'],))
            c.execute('UPDATE project_pools SET revision=revision+1 WHERE project_id=?', (project['id'],))
        _audit(c, scope['board_id'], agent, 'archive' if data.get('archived') else 'update', data)
        _event(c, scope['board_id'], agent, 'Project settings changed.' if not group_slug else 'Group settings changed.',
               {'action': 'settings_changed'})
    project = find_project(slug)
    return describe_group(project, find_group(project, group_slug), agent) if group_slug else describe_project(project, agent)


def get_scope(slug, group_slug=None):
    project = find_project(slug)
    scope = find_group(project, group_slug) if group_slug else project
    return project, scope


def active_scope(project, scope):
    if project['archived'] or scope['archived']:
        raise Conflict('Archived context is read-only.')


def save_context(slug, group_slug, data, agent):
    with db._write_lock, db.conn() as c:
        project, scope = get_scope(slug, group_slug)
        active_scope(project, scope)
        current = context(scope['board_id'])
        if data['base_revision'] != current['revision']:
            raise Conflict('Context changed; reload the current revision and reconcile your draft.')
        publish = data['mode'] in ('publish', 'empty')
        manager = can_manage(project, agent)
        if publish and not manager:
            raise PermissionError('Bots propose drafts; a human project operator approves publication.')
        if not publish and c.execute("SELECT COUNT(*) FROM context_revisions WHERE board_id=? AND status='draft'",
                                     (scope['board_id'],)).fetchone()[0] >= 10:
            raise Conflict('This scope has 10 pending drafts; review or discard them before adding another.')
        if data['mode'] == 'empty' and (data['body'] or data['sources']):
            raise ValueError('An explicit empty seed cannot contain body or sources.')
        if data['mode'] != 'empty' and not data['body'].strip():
            raise ValueError('Context body is required; use empty mode to approve an empty seed.')
        if publish and data['mode'] != 'empty' and not data['sources']:
            raise ValueError('Published context must reference its sources.')
        revision = current['revision'] + 1 if publish else None
        cid = c.execute('INSERT INTO context_revisions(board_id,base_revision,revision,status,body,sources,reason,'
            'author_id,approved_by,created_at,published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (scope['board_id'], current['revision'], revision, 'published' if publish else 'draft', data['body'],
             json.dumps(data['sources']), data['reason'], agent['id'], agent['id'] if publish and manager else None,
             db.now(), db.now() if publish else None)).lastrowid
        _audit(c, scope['board_id'], agent, 'publish_context' if publish else 'propose_context', {'id': cid})
        _event(c, scope['board_id'], agent, 'Context published.' if publish else 'Context draft proposed.',
               {'revision': revision, 'context_id': cid})
    return {'context': context(scope['board_id']), 'id': cid, 'readiness': readiness(scope['board_id'])}


def revision_detail(board_id, rid):
    row = db.conn().execute('SELECT r.*, a.handle author FROM context_revisions r JOIN agents a'
        ' ON a.id=r.author_id WHERE r.board_id=? AND r.id=?', (board_id, rid)).fetchone()
    if row is None:
        raise KeyError('No such context revision in this scope.')
    result = dict(row)
    result['sources'] = json.loads(result['sources'])
    for key in ('author_id', 'board_id', 'approved_by'):
        result.pop(key)
    return result


def list_revisions(board_id, before=None, limit=20):
    rows = db.conn().execute('SELECT r.id,r.base_revision,r.revision,r.status,r.created_at,a.handle author,'
        ' substr(r.body,1,160) preview FROM context_revisions r JOIN agents a ON a.id=r.author_id'
        ' WHERE r.board_id=? AND (? IS NULL OR r.id<?) ORDER BY r.id DESC LIMIT ?',
        (board_id, before, before, limit + 1)).fetchall()
    return {'revisions': [dict(r) for r in rows[:limit]], 'has_more': len(rows) > limit,
            'next_before': rows[limit - 1]['id'] if len(rows) > limit else None}


def approve_context(slug, group_slug, rid, expected, agent):
    with db._write_lock, db.conn() as c:
        project, scope = get_scope(slug, group_slug)
        require_manage(project, agent)
        active_scope(project, scope)
        draft = revision_detail(scope['board_id'], rid)
        current = context(scope['board_id'])
        if draft['status'] != 'draft' or expected != current['revision'] or draft['base_revision'] != expected:
            raise Conflict('Draft or current context changed; reconcile before approval.')
        if not draft['sources']:
            raise ValueError('A draft must reference its sources before approval; use explicit empty mode otherwise.')
        c.execute("UPDATE context_revisions SET status='published', revision=?, approved_by=?, published_at=? WHERE id=?",
                  (expected + 1, agent['id'], db.now(), rid))
        _audit(c, scope['board_id'], agent, 'approve_context', {'id': rid})
        _event(c, scope['board_id'], agent, 'Context draft approved.', {'context_id': rid, 'revision': expected + 1})
    return {'context': context(scope['board_id']), 'readiness': 'ready'}


def discard_context(slug, group_slug, rid, agent):
    with db._write_lock, db.conn() as c:
        project, scope = get_scope(slug, group_slug)
        require_manage(project, agent)
        active_scope(project, scope)
        if revision_detail(scope['board_id'], rid)['status'] != 'draft':
            raise ValueError('Only pending drafts can be discarded.')
        c.execute("UPDATE context_revisions SET status='discarded' WHERE id=?", (rid,))
        _audit(c, scope['board_id'], agent, 'discard_context', {'id': rid})
        _event(c, scope['board_id'], agent, 'Context draft discarded.', {'context_id': rid})
    return {'readiness': readiness(scope['board_id'])}


def seed_group(slug, group_slug, sources, search_scope, agent):
    with db._write_lock, db.conn() as c:
        project, group = get_scope(slug, group_slug)
        require_manage(project, agent)
        active_scope(project, group)
        if group['seed_thread_id'] and not context(group['board_id'])['revision']:
            return db.get_message(group['seed_thread_id'])
        cutoff = db.cursor()
        body = (f"Context seeding requested for {project['name']} / {group['name']}.\n"
                f"Search scope: {search_scope}\nSources:\n" + '\n'.join(sources) +
                "\nClaim this thread before curating earlier work. Produce a context draft with established decisions,"
                " approved assets, successful/failed approaches, dependencies, source links, conflicts and coverage gaps."
                " Do not treat experiments as approved decisions. Do not rerender assets unless separately asked."
                " Submit the draft to the group's /context API using its current base_revision; a human approves it."
                " Reply in this thread with the draft ID and outcome, keeping meta.kind=context_seed for seeding replies.")
        mid = c.execute('INSERT INTO messages(board_id,agent_id,body,meta,created_at) VALUES (?,?,?,?,?)',
              (group['board_id'], agent['id'], body, json.dumps({'kind': 'context_seed', 'sources': sources,
               'scope': search_scope, 'source_cutoff': cutoff, 'project': slug, 'group': group_slug}), db.now())).lastrowid
        c.execute('UPDATE messages SET thread_id=id WHERE id=?', (mid,))
        c.execute('UPDATE project_groups SET seed_thread_id=? WHERE id=?', (mid, group['id']))
        _audit(c, group['board_id'], agent, 'request_seed', {'thread_id': mid, 'sources': sources, 'cutoff': cutoff})
    return db.get_message(mid)


def route(channel):
    row = db.conn().execute('SELECT p.slug project_slug,g.slug group_slug FROM projects p'
        ' LEFT JOIN project_groups g ON g.project_id=p.id'
        ' JOIN boards b ON b.id=p.board_id OR b.id=g.board_id WHERE b.slug=? LIMIT 1', (channel,)).fetchone()
    if row is None:
        return {'project': None, 'group': None}
    project = find_project(row['project_slug'])
    own_channel = db.get_board_by_id(project['board_id'])['slug']
    group = find_group(project, row['group_slug']) if channel != own_channel else None
    described = describe_project(project)
    described.pop('groups')
    return {'project': described, 'group': describe_group(project, group) if group else None}


def is_archived(board_id):
    return db.conn().execute('SELECT 1 FROM projects p LEFT JOIN project_groups g ON g.project_id=p.id'
        ' WHERE (p.board_id=? AND p.archived=1) OR (g.board_id=? AND (p.archived=1 OR g.archived=1)) LIMIT 1',
        (board_id, board_id)).fetchone() is not None


def pool_settings(project):
    row = db.conn().execute('SELECT configured,revision FROM project_pools WHERE project_id=?',
                            (project['id'],)).fetchone()
    bots = [r['handle'] for r in db.conn().execute(
        'SELECT a.handle FROM project_pool_bots pb JOIN agents a ON a.id=pb.agent_id'
        ' WHERE pb.project_id=? ORDER BY a.handle COLLATE NOCASE', (project['id'],))]
    return {'configured': bool(row['configured']) if row else False,
            'revision': row['revision'] if row else 0, 'bots': bots}


def assigned_project(agent):
    row = db.conn().execute('SELECT p.slug FROM project_pool_bots pb JOIN projects p ON p.id=pb.project_id'
        ' WHERE pb.agent_id=? AND p.archived=0', (agent['id'],)).fetchone()
    return row['slug'] if row else None


def _pool_candidates():
    from . import fleet
    assigned = {r['handle'].lower(): r['slug'] for r in db.conn().execute(
        'SELECT a.handle,p.slug FROM project_pool_bots pb JOIN agents a ON a.id=pb.agent_id'
        ' JOIN projects p ON p.id=pb.project_id WHERE p.archived=0')}
    candidates = {}
    for host in fleet.hosts():
        for bot in host['bots']:
            if not bot.get('pool') or not db.get_agent_by_handle(bot['handle']):
                continue
            key = bot['handle'].lower()
            available = bool(host['reporting'] and bot['status'].startswith(fleet.UP) and not bot.get('move_to'))
            item = {'handle': bot['handle'], 'host': host['name'], 'status': bot['status'],
                    'available': available, 'assigned_project': assigned.get(key)}
            if key not in candidates or available:
                candidates[key] = item
    return candidates


def describe_pool(slug, agent, after='', limit=50):
    project = find_project(slug)
    settings = pool_settings(project)
    candidates = _pool_candidates()
    for handle in settings['bots']:
        candidates.setdefault(handle.lower(), {'handle': handle, 'host': '', 'status': 'not reporting',
            'available': False, 'assigned_project': slug})
    keys = sorted(k for k in candidates if k > after.lower())
    return {**settings, 'can_manage': can_manage(project, agent),
            'candidates': [candidates[k] for k in keys[:limit]], 'has_more': len(keys) > limit,
            'next_after': keys[limit - 1] if len(keys) > limit else None}


def set_pool(slug, handles, configured, expected, agent):
    with db._write_lock, db.conn() as c:
        project = find_project(slug)
        require_manage(project, agent)
        active_scope(project, project)
        previous = pool_settings(project)
        if previous['revision'] != expected:
            raise Conflict('Project pool changed; reload before saving your selection.')
        if handles and not configured:
            raise ValueError('Shared-pool mode cannot have dedicated bots selected.')
        candidates = _pool_candidates()
        previous_handles = {h.lower() for h in previous['bots']}
        ids = []
        for handle in handles:
            bot = db.get_agent_by_handle(handle)
            if not bot or (handle.lower() not in candidates and handle.lower() not in previous_handles):
                raise ValueError(f'{handle} is not a keeper-reported pool bot.')
            assigned = assigned_project(bot)
            if assigned and assigned != slug:
                raise Conflict(f'{handle} is already dedicated to {assigned}; release it there first.')
            ids.append(bot['id'])
        c.execute('INSERT INTO project_pools(project_id,configured,revision) VALUES (?,?,?)'
            ' ON CONFLICT(project_id) DO UPDATE SET configured=excluded.configured,revision=excluded.revision',
            (project['id'], configured, expected + 1))
        c.execute('DELETE FROM project_pool_bots WHERE project_id=?', (project['id'],))
        c.executemany('INSERT INTO project_pool_bots(agent_id,project_id) VALUES (?,?)',
                      [(aid, project['id']) for aid in ids])
        _audit(c, project['board_id'], agent, 'set_bot_pool', {'configured': configured, 'bots': handles})
        # Wake the newly eligible pool to inspect existing work (all shared bots when released).
        _event(c, project['board_id'], agent,
            'Project bot pool changed. Allocated bots: review current project/group context and open threads for'
            ' unclaimed work before taking a task. Existing work is not cancelled.',
            {'kind': 'project_pool', 'configured': configured, 'bots': handles})
    return describe_pool(slug, agent)
