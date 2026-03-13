from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, decode_token
import os, uuid, secrets, random
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///nexuschat.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
jwt = JWTManager(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

ALLOWED = {'png','jpg','jpeg','gif','webp','mp4','pdf','txt','zip','mp3','wav','svg'}
MEDIA   = {'png','jpg','jpeg','gif','webp','svg'}
COLORS  = ['#7c3aed','#2563eb','#dc2626','#059669','#d97706','#db2777','#0891b2','#ea580c','#65a30d']
XOKRAM  = 'Xokram'

def allowed(f):
    return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED

# ── MODELS ──────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'user'
    id            = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username      = db.Column(db.String(50),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    avatar_color  = db.Column(db.String(20),  default='#7c3aed')
    avatar_url    = db.Column(db.String(500), nullable=True)
    banner_url    = db.Column(db.String(500), nullable=True)
    bio           = db.Column(db.String(200), nullable=True)
    boost_count   = db.Column(db.Integer,     default=5)
    status        = db.Column(db.String(20),  default='offline')
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        try:
            st = UserStatus.query.filter_by(user_id=self.id).first()
            status_data = None
            if st and st.expires_at > datetime.utcnow():
                status_data = {
                    'text': st.text or '',
                    'image_url': st.image_url,
                    'created_at': st.created_at.isoformat(),
                    'expires_at': st.expires_at.isoformat()
                }
        except Exception:
            status_data = None
        return {
            'id': self.id, 'username': self.username,
            'avatar_color': self.avatar_color, 'avatar_url': self.avatar_url,
            'banner_url': self.banner_url, 'bio': self.bio or '',
            'boost_count': self.boost_count or 5, 'status': self.status,
            'user_status': status_data
        }

class UserStatus(db.Model):
    __tablename__ = 'user_status'
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = db.Column(db.String(36), db.ForeignKey('user.id'), unique=True, nullable=False)
    text       = db.Column(db.String(100), nullable=True)
    image_url  = db.Column(db.String(500), nullable=True)
    expires_at = db.Column(db.DateTime,    nullable=False)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

class Friendship(db.Model):
    __tablename__ = 'friendship'
    id          = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sender_id   = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    status      = db.Column(db.String(20), default='pending')
    created_at  = db.Column(db.DateTime,   default=datetime.utcnow)

class Server(db.Model):
    __tablename__ = 'server'
    id          = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    name        = db.Column(db.String(100), nullable=False)
    owner_id    = db.Column(db.String(36),  db.ForeignKey('user.id'), nullable=False)
    invite_code = db.Column(db.String(20),  unique=True, default=lambda: secrets.token_urlsafe(8))
    icon_color  = db.Column(db.String(20),  default='#7c3aed')
    icon_url    = db.Column(db.String(500), nullable=True)
    banner_url  = db.Column(db.String(500), nullable=True)
    description = db.Column(db.String(200), nullable=True)
    boost_count = db.Column(db.Integer,     default=0)
    verified    = db.Column(db.Boolean,     default=False)
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        mc = ServerMember.query.filter_by(server_id=self.id).count()
        return {
            'id': self.id, 'name': self.name, 'owner_id': self.owner_id,
            'invite_code': self.invite_code, 'icon_color': self.icon_color,
            'icon_url': self.icon_url, 'banner_url': self.banner_url,
            'description': self.description or '', 'boost_count': self.boost_count or 0,
            'verified': bool(self.verified), 'member_count': mc
        }

class ServerBoost(db.Model):
    __tablename__ = 'server_boost'
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id  = db.Column(db.String(36), db.ForeignKey('server.id'), nullable=False)
    user_id    = db.Column(db.String(36), db.ForeignKey('user.id'),   nullable=False)
    created_at = db.Column(db.DateTime,   default=datetime.utcnow)

class ServerMember(db.Model):
    __tablename__ = 'server_member'
    id        = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id = db.Column(db.String(36), db.ForeignKey('server.id'), nullable=False)
    user_id   = db.Column(db.String(36), db.ForeignKey('user.id'),   nullable=False)
    role      = db.Column(db.String(20), default='member')

class Role(db.Model):
    __tablename__ = 'role'
    id                  = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id           = db.Column(db.String(36),  db.ForeignKey('server.id'), nullable=False)
    name                = db.Column(db.String(50),  nullable=False)
    color               = db.Column(db.String(20),  default='#99aab5')
    position            = db.Column(db.Integer,     default=0)
    can_manage_channels = db.Column(db.Boolean,     default=False)
    can_manage_members  = db.Column(db.Boolean,     default=False)
    created_at          = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'server_id': self.server_id, 'name': self.name,
            'color': self.color, 'position': self.position,
            'can_manage_channels': bool(self.can_manage_channels),
            'can_manage_members': bool(self.can_manage_members)
        }

class MemberRole(db.Model):
    __tablename__ = 'member_role'
    id        = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id = db.Column(db.String(36), db.ForeignKey('server.id'), nullable=False)
    user_id   = db.Column(db.String(36), db.ForeignKey('user.id'),   nullable=False)
    role_id   = db.Column(db.String(36), db.ForeignKey('role.id'),   nullable=False)

class Channel(db.Model):
    __tablename__ = 'channel'
    id           = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id    = db.Column(db.String(36),  db.ForeignKey('server.id'), nullable=False)
    name         = db.Column(db.String(100), nullable=False)
    channel_type = db.Column(db.String(20),  default='text')
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'server_id': self.server_id, 'name': self.name, 'type': self.channel_type}

class Message(db.Model):
    __tablename__ = 'message'
    id         = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    channel_id = db.Column(db.String(36),  db.ForeignKey('channel.id'), nullable=True)
    dm_room_id = db.Column(db.String(100), nullable=True)
    sender_id  = db.Column(db.String(36),  db.ForeignKey('user.id'), nullable=False)
    content    = db.Column(db.Text,        nullable=True)
    file_url   = db.Column(db.String(500), nullable=True)
    file_name  = db.Column(db.String(200), nullable=True)
    file_type  = db.Column(db.String(50),  nullable=True)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        sender = User.query.get(self.sender_id)
        reactions = {}
        for r in Reaction.query.filter_by(message_id=self.id).all():
            reactions[r.emoji] = reactions.get(r.emoji, 0) + 1
        return {
            'id': self.id, 'channel_id': self.channel_id, 'dm_room_id': self.dm_room_id,
            'sender': sender.to_dict() if sender else None,
            'content': self.content, 'file_url': self.file_url,
            'file_name': self.file_name, 'file_type': self.file_type,
            'reactions': reactions, 'created_at': self.created_at.isoformat()
        }

class Reaction(db.Model):
    __tablename__ = 'reaction'
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id = db.Column(db.String(36), db.ForeignKey('message.id'), nullable=False)
    user_id    = db.Column(db.String(36), db.ForeignKey('user.id'),    nullable=False)
    emoji      = db.Column(db.String(10), nullable=False)

# ── ADD MISSING COLUMNS TO OLD DATABASES ───────────────────────────────────
def migrate_db():
    """Add new columns to existing tables if they don't exist."""
    from sqlalchemy import text, inspect
    insp = inspect(db.engine)
    
    def has_col(table, col):
        try:
            cols = [c['name'] for c in insp.get_columns(table)]
            return col in cols
        except:
            return True
    
    def has_table(table):
        try:
            return insp.has_table(table)
        except:
            return False
    
    with db.engine.connect() as conn:
        if has_table('user'):
            for col, typ in [('avatar_url','VARCHAR(500)'),('banner_url','VARCHAR(500)'),
                              ('bio','VARCHAR(200)'),('boost_count','INTEGER DEFAULT 5')]:
                if not has_col('user', col):
                    try: conn.execute(text(f'ALTER TABLE "user" ADD COLUMN {col} {typ}'))
                    except: pass
        if has_table('server'):
            for col, typ in [('icon_url','VARCHAR(500)'),('banner_url','VARCHAR(500)'),
                              ('description','VARCHAR(200)'),('boost_count','INTEGER DEFAULT 0'),
                              ('verified','BOOLEAN DEFAULT FALSE')]:
                if not has_col('server', col):
                    try: conn.execute(text(f'ALTER TABLE "server" ADD COLUMN {col} {typ}'))
                    except: pass
        try:
            conn.commit()
        except:
            pass

# ── ROUTES ──────────────────────────────────────────────────────────────────

@app.route('/')
def index(): return render_template('index.html')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/register', methods=['POST'])
def register():
    try:
        d = request.get_json()
        username = (d.get('username') or '').strip()
        email    = (d.get('email')    or '').strip().lower()
        password =  d.get('password') or ''
        if not username or not email or not password: return jsonify({'error':'Faltan campos'}),400
        if len(username)<3 or len(username)>20: return jsonify({'error':'Username 3-20 chars'}),400
        if User.query.filter_by(username=username).first(): return jsonify({'error':'Username en uso'}),400
        if User.query.filter_by(email=email).first(): return jsonify({'error':'Email ya registrado'}),400
        if len(password)<6: return jsonify({'error':'Contraseña mínimo 6 caracteres'}),400
        user = User(username=username, email=email,
                    password_hash=bcrypt.generate_password_hash(password).decode(),
                    avatar_color=random.choice(COLORS))
        db.session.add(user); db.session.commit()
        return jsonify({'token': create_access_token(identity=user.id), 'user': user.to_dict()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        d = request.get_json()
        ident = (d.get('username') or '').strip(); pw = d.get('password') or ''
        user = User.query.filter((User.username==ident)|(User.email==ident.lower())).first()
        if not user or not bcrypt.check_password_hash(user.password_hash, pw):
            return jsonify({'error':'Usuario o contraseña incorrectos'}),401
        user.status='online'; db.session.commit()
        return jsonify({'token': create_access_token(identity=user.id), 'user': user.to_dict()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/me')
@jwt_required()
def get_me():
    try:
        u = User.query.get(get_jwt_identity())
        return jsonify(u.to_dict()) if u else (jsonify({'error':'Not found'}),404)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/me/profile', methods=['PATCH'])
@jwt_required()
def update_profile():
    user = User.query.get(get_jwt_identity()); d = request.get_json()
    if 'bio' in d: user.bio = (d['bio'] or '')[:200]
    db.session.commit(); return jsonify(user.to_dict())

def _save_media(field, prefix):
    if field not in request.files: return None, 'Sin archivo'
    file = request.files[field]
    ext  = (file.filename or '').rsplit('.',1)[-1].lower()
    if ext not in MEDIA: return None, 'Solo imágenes/GIFs'
    fname = f"{prefix}_{uuid.uuid4()}.{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
    return f'/uploads/{fname}', None

@app.route('/api/me/avatar', methods=['POST'])
@jwt_required()
def upload_my_avatar():
    user = User.query.get(get_jwt_identity())
    url, err = _save_media('file','av')
    if err: return jsonify({'error':err}),400
    user.avatar_url = url; db.session.commit()
    return jsonify(user.to_dict())

@app.route('/api/me/banner', methods=['POST'])
@jwt_required()
def upload_my_banner():
    user = User.query.get(get_jwt_identity())
    url, err = _save_media('file','bn')
    if err: return jsonify({'error':err}),400
    user.banner_url = url; db.session.commit()
    return jsonify(user.to_dict())

@app.route('/api/me/status', methods=['POST'])
@jwt_required()
def set_status():
    uid = get_jwt_identity()
    text = (request.form.get('text') or '')[:100]
    st = UserStatus.query.filter_by(user_id=uid).first()
    image_url = None
    if 'file' in request.files:
        url, err = _save_media('file', 'st')
        if not err: image_url = url
    if not st:
        st = UserStatus(user_id=uid, text=text, image_url=image_url,
                        expires_at=datetime.utcnow()+timedelta(hours=12))
        db.session.add(st)
    else:
        st.text=text; st.created_at=datetime.utcnow()
        st.expires_at=datetime.utcnow()+timedelta(hours=12)
        if image_url: st.image_url=image_url
    db.session.commit()
    return jsonify(User.query.get(uid).to_dict())

@app.route('/api/me/status', methods=['DELETE'])
@jwt_required()
def delete_status():
    uid = get_jwt_identity()
    st = UserStatus.query.filter_by(user_id=uid).first()
    if st: db.session.delete(st); db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/friends/request', methods=['POST'])
@jwt_required()
def send_friend_request():
    uid = get_jwt_identity(); tu = (request.get_json().get('username') or '').strip()
    t = User.query.filter_by(username=tu).first()
    if not t: return jsonify({'error':'Usuario no encontrado'}),404
    if t.id==uid: return jsonify({'error':'No puedes añadirte a ti mismo'}),400
    ex = Friendship.query.filter(
        ((Friendship.sender_id==uid)&(Friendship.receiver_id==t.id))|
        ((Friendship.sender_id==t.id)&(Friendship.receiver_id==uid))).first()
    if ex: return jsonify({'error':'Ya existe solicitud o ya son amigos'}),400
    f = Friendship(sender_id=uid, receiver_id=t.id)
    db.session.add(f); db.session.commit()
    socketio.emit('friend_request',{'from':User.query.get(uid).to_dict(),'friendship_id':f.id},room=f'user_{t.id}')
    return jsonify({'message':'Solicitud enviada'})

@app.route('/api/friends/accept/<fid>', methods=['POST'])
@jwt_required()
def accept_friend(fid):
    uid = get_jwt_identity(); f = Friendship.query.get(fid)
    if not f or f.receiver_id!=uid: return jsonify({'error':'No encontrado'}),404
    f.status='accepted'; db.session.commit()
    socketio.emit('friend_accepted',{'friend':User.query.get(uid).to_dict()},room=f'user_{f.sender_id}')
    return jsonify({'message':'Aceptado','friend':User.query.get(f.sender_id).to_dict()})

@app.route('/api/friends/reject/<fid>', methods=['POST'])
@jwt_required()
def reject_friend(fid):
    uid=get_jwt_identity(); f=Friendship.query.get(fid)
    if not f or f.receiver_id!=uid: return jsonify({'error':'No encontrado'}),404
    db.session.delete(f); db.session.commit(); return jsonify({'message':'Rechazado'})

@app.route('/api/friends')
@jwt_required()
def get_friends():
    uid = get_jwt_identity()
    fs = Friendship.query.filter(
        ((Friendship.sender_id==uid)|(Friendship.receiver_id==uid))&(Friendship.status=='accepted')).all()
    friends = []
    for f in fs:
        fid = f.receiver_id if f.sender_id==uid else f.sender_id
        u=User.query.get(fid)
        if u: friends.append(u.to_dict())
    pending = []
    for f in Friendship.query.filter_by(receiver_id=uid,status='pending').all():
        u=User.query.get(f.sender_id)
        if u: pending.append({'friendship_id':f.id,'user':u.to_dict()})
    return jsonify({'friends':friends,'pending':pending})

@app.route('/api/servers')
@jwt_required()
def get_servers():
    uid = get_jwt_identity()
    return jsonify([Server.query.get(m.server_id).to_dict()
                    for m in ServerMember.query.filter_by(user_id=uid).all()
                    if Server.query.get(m.server_id)])

@app.route('/api/servers', methods=['POST'])
@jwt_required()
def create_server():
    uid=get_jwt_identity(); name=(request.get_json().get('name') or '').strip()
    if not name: return jsonify({'error':'Nombre requerido'}),400
    s = Server(name=name,owner_id=uid,icon_color=random.choice(COLORS))
    db.session.add(s); db.session.flush()
    db.session.add(ServerMember(server_id=s.id,user_id=uid,role='owner'))
    db.session.add(Channel(server_id=s.id,name='general'))
    db.session.add(Channel(server_id=s.id,name='bienvenida'))
    db.session.commit(); return jsonify(s.to_dict())

@app.route('/api/servers/<sid>', methods=['PATCH'])
@jwt_required()
def update_server(sid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    s=Server.query.get(sid); d=request.get_json()
    if 'name' in d and d['name'].strip(): s.name=d['name'].strip()[:100]
    if 'description' in d: s.description=(d['description'] or '')[:200]
    db.session.commit(); return jsonify(s.to_dict())

@app.route('/api/servers/<sid>/icon', methods=['POST'])
@jwt_required()
def upload_server_icon(sid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    s=Server.query.get(sid); url,err=_save_media('file','si')
    if err: return jsonify({'error':err}),400
    s.icon_url=url; db.session.commit(); return jsonify(s.to_dict())

@app.route('/api/servers/<sid>/banner', methods=['POST'])
@jwt_required()
def upload_server_banner(sid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    s=Server.query.get(sid); url,err=_save_media('file','sb')
    if err: return jsonify({'error':err}),400
    s.banner_url=url; db.session.commit(); return jsonify(s.to_dict())

@app.route('/api/servers/<sid>/verify', methods=['POST'])
@jwt_required()
def verify_server(sid):
    uid=get_jwt_identity(); user=User.query.get(uid)
    if not user or user.username != XOKRAM:
        return jsonify({'error':'Solo Xokram puede verificar servidores'}),403
    s=Server.query.get(sid)
    if not s: return jsonify({'error':'No encontrado'}),404
    s.verified = not bool(s.verified); db.session.commit()
    socketio.emit('server_verified',{'server':s.to_dict()},room=f'server_{sid}')
    return jsonify(s.to_dict())

@app.route('/api/servers/<sid>/boost', methods=['POST'])
@jwt_required()
def boost_server(sid):
    uid=get_jwt_identity(); user=User.query.get(uid); s=Server.query.get(sid)
    if not s: return jsonify({'error':'Servidor no encontrado'}),404
    if not ServerMember.query.filter_by(server_id=sid,user_id=uid).first():
        return jsonify({'error':'No eres miembro'}),403
    already=ServerBoost.query.filter_by(server_id=sid,user_id=uid).first()
    if already:
        db.session.delete(already)
        s.boost_count=max(0,(s.boost_count or 0)-1)
        user.boost_count=(user.boost_count or 0)+1
        db.session.commit()
        return jsonify({'boosted':False,'server_boosts':s.boost_count,'user_boosts':user.boost_count})
    if (user.boost_count or 0)<=0: return jsonify({'error':'No te quedan boosts'}),400
    db.session.add(ServerBoost(server_id=sid,user_id=uid))
    s.boost_count=(s.boost_count or 0)+1; user.boost_count=(user.boost_count or 5)-1; db.session.commit()
    socketio.emit('server_boosted',{'server':s.to_dict()},room=f'server_{sid}')
    return jsonify({'boosted':True,'server_boosts':s.boost_count,'user_boosts':user.boost_count})

@app.route('/api/invite/<code>')
def get_invite_info(code):
    s=Server.query.filter_by(invite_code=code).first()
    if not s: return jsonify({'error':'Código inválido'}),404
    return jsonify(s.to_dict())

@app.route('/api/servers/join/<code>', methods=['POST'])
@jwt_required()
def join_server(code):
    uid=get_jwt_identity(); s=Server.query.filter_by(invite_code=code).first()
    if not s: return jsonify({'error':'Código inválido'}),404
    if ServerMember.query.filter_by(server_id=s.id,user_id=uid).first():
        return jsonify({'error':'Ya eres miembro'}),400
    db.session.add(ServerMember(server_id=s.id,user_id=uid)); db.session.commit()
    return jsonify(s.to_dict())

@app.route('/api/servers/<sid>/channels')
@jwt_required()
def get_channels(sid):
    uid=get_jwt_identity()
    if not ServerMember.query.filter_by(server_id=sid,user_id=uid).first():
        return jsonify({'error':'No eres miembro'}),403
    return jsonify([c.to_dict() for c in Channel.query.filter_by(server_id=sid).order_by(Channel.created_at).all()])

@app.route('/api/servers/<sid>/channels', methods=['POST'])
@jwt_required()
def create_channel(sid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    d=request.get_json(); name=(d.get('name') or 'nuevo-canal').lower().replace(' ','-')[:40]
    ctype=d.get('type','text')
    ch=Channel(server_id=sid,name=name,channel_type=ctype); db.session.add(ch); db.session.commit()
    return jsonify(ch.to_dict())

@app.route('/api/servers/<sid>/members')
@jwt_required()
def get_members(sid):
    uid=get_jwt_identity()
    if not ServerMember.query.filter_by(server_id=sid,user_id=uid).first():
        return jsonify({'error':'No eres miembro'}),403
    result=[]
    for m in ServerMember.query.filter_by(server_id=sid).all():
        u=User.query.get(m.user_id)
        if u:
            d=u.to_dict(); d['role']=m.role
            member_roles=[]
            try:
                for mr in MemberRole.query.filter_by(server_id=sid,user_id=m.user_id).all():
                    r=Role.query.get(mr.role_id)
                    if r: member_roles.append(r.to_dict())
            except: pass
            d['roles']=member_roles; result.append(d)
    return jsonify(result)

@app.route('/api/servers/<sid>/roles')
@jwt_required()
def get_roles(sid):
    uid=get_jwt_identity()
    if not ServerMember.query.filter_by(server_id=sid,user_id=uid).first():
        return jsonify({'error':'No eres miembro'}),403
    try:
        roles=Role.query.filter_by(server_id=sid).order_by(Role.position.desc()).all()
        return jsonify([r.to_dict() for r in roles])
    except:
        return jsonify([])

@app.route('/api/servers/<sid>/roles', methods=['POST'])
@jwt_required()
def create_role(sid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    d=request.get_json()
    pos=Role.query.filter_by(server_id=sid).count()
    r=Role(server_id=sid,name=(d.get('name') or 'Rol')[:50],color=d.get('color','#99aab5'),
           position=pos,can_manage_channels=bool(d.get('can_manage_channels')),
           can_manage_members=bool(d.get('can_manage_members')))
    db.session.add(r); db.session.commit()
    return jsonify(r.to_dict())

@app.route('/api/servers/<sid>/roles/<rid>', methods=['PATCH'])
@jwt_required()
def update_role(sid,rid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    r=Role.query.get(rid)
    if not r or r.server_id!=sid: return jsonify({'error':'No encontrado'}),404
    d=request.get_json()
    if 'name' in d: r.name=d['name'][:50]
    if 'color' in d: r.color=d['color']
    if 'can_manage_channels' in d: r.can_manage_channels=bool(d['can_manage_channels'])
    if 'can_manage_members' in d: r.can_manage_members=bool(d['can_manage_members'])
    db.session.commit(); return jsonify(r.to_dict())

@app.route('/api/servers/<sid>/roles/<rid>', methods=['DELETE'])
@jwt_required()
def delete_role(sid,rid):
    uid=get_jwt_identity(); m=ServerMember.query.filter_by(server_id=sid,user_id=uid).first()
    if not m or m.role not in ['owner','admin']: return jsonify({'error':'Sin permisos'}),403
    r=Role.query.get(rid)
    if not r or r.server_id!=sid: return jsonify({'error':'No encontrado'}),404
    MemberRole.query.filter_by(role_id=rid).delete()
    db.session.delete(r); db.session.commit(); return jsonify({'ok':True})

@app.route('/api/channels/<cid>/messages')
@jwt_required()
def get_channel_messages(cid):
    uid=get_jwt_identity(); ch=Channel.query.get(cid)
    if not ch: return jsonify({'error':'No encontrado'}),404
    if not ServerMember.query.filter_by(server_id=ch.server_id,user_id=uid).first():
        return jsonify({'error':'Sin acceso'}),403
    return jsonify([m.to_dict() for m in Message.query.filter_by(channel_id=cid).order_by(Message.created_at).limit(100).all()])

@app.route('/api/dm/<fid>/messages')
@jwt_required()
def get_dm_messages(fid):
    uid=get_jwt_identity(); room='_'.join(sorted([uid,fid]))
    return jsonify([m.to_dict() for m in Message.query.filter_by(dm_room_id=room).order_by(Message.created_at).limit(100).all()])

@app.route('/api/upload', methods=['POST'])
@jwt_required()
def upload_file():
    if 'file' not in request.files: return jsonify({'error':'Sin archivo'}),400
    file=request.files['file']
    if not file.filename or not allowed(file.filename): return jsonify({'error':'Tipo no permitido'}),400
    ext=file.filename.rsplit('.',1)[1].lower(); fname=f"{uuid.uuid4()}.{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'],fname))
    return jsonify({'url':f'/uploads/{fname}','name':file.filename,'type':ext})

@app.route('/api/messages/<mid>/react', methods=['POST'])
@jwt_required()
def react(mid):
    uid=get_jwt_identity(); emoji=(request.get_json().get('emoji') or '')[:10]
    ex=Reaction.query.filter_by(message_id=mid,user_id=uid,emoji=emoji).first()
    if ex: db.session.delete(ex)
    else: db.session.add(Reaction(message_id=mid,user_id=uid,emoji=emoji))
    db.session.commit()
    counts={}
    for r in Reaction.query.filter_by(message_id=mid).all(): counts[r.emoji]=counts.get(r.emoji,0)+1
    msg=Message.query.get(mid)
    room=f'channel_{msg.channel_id}' if msg.channel_id else f'dm_{msg.dm_room_id}'
    socketio.emit('reaction_update',{'message_id':mid,'reactions':counts},room=room)
    return jsonify({'reactions':counts})

# ── SOCKETS ─────────────────────────────────────────────────────────────────

@socketio.on('authenticate')
def on_authenticate(data):
    try:
        uid=decode_token(data.get('token',''))['sub']; user=User.query.get(uid)
        if user:
            join_room(f'user_{uid}'); user.status='online'; db.session.commit()
            emit('authenticated',{'user':user.to_dict()})
    except Exception as e: emit('auth_error',{'error':str(e)})

@socketio.on('join_server_room')
def on_join_server_room(data): join_room(f'server_{data.get("server_id")}')

@socketio.on('join_channel')
def on_join_channel(data): join_room(f'channel_{data.get("channel_id")}')

@socketio.on('leave_channel')
def on_leave_channel(data): leave_room(f'channel_{data.get("channel_id")}')

@socketio.on('join_dm')
def on_join_dm(data): join_room(f'dm_{data.get("room_id")}')

@socketio.on('send_message')
def on_send_message(data):
    try:
        uid=decode_token(data.get('token',''))['sub']
        content=(data.get('content') or '').strip(); file_url=data.get('file_url')
        if not content and not file_url: return
        msg=Message(channel_id=data.get('channel_id'),dm_room_id=data.get('dm_room_id'),
                    sender_id=uid,content=content or None,file_url=file_url,
                    file_name=data.get('file_name'),file_type=data.get('file_type'))
        db.session.add(msg); db.session.commit()
        md=msg.to_dict()
        if msg.channel_id: emit('new_message',md,room=f'channel_{msg.channel_id}')
        elif msg.dm_room_id: emit('new_message',md,room=f'dm_{msg.dm_room_id}')
    except Exception as e: emit('error',{'error':str(e)})

@socketio.on('typing')
def on_typing(data):
    try:
        uid=decode_token(data.get('token',''))['sub']; user=User.query.get(uid)
        if user: emit('user_typing',{'username':user.username},room=data.get('room'),include_self=False)
    except: pass

@socketio.on('call_user')
def on_call_user(data):
    try:
        uid=decode_token(data.get('token',''))['sub']; user=User.query.get(uid)
        if user: emit('call_incoming',{'from':user.to_dict(),'call_id':data.get('call_id')},room=f'user_{data.get("target_id")}')
    except: pass

@socketio.on('call_accepted')
def on_call_accepted(data):
    try:
        uid=decode_token(data.get('token',''))['sub']; user=User.query.get(uid)
        if user: emit('call_accepted',{'from':user.to_dict()},room=f'user_{data.get("caller_id")}')
    except: pass

@socketio.on('call_rejected')
def on_call_rejected(data):
    try:
        emit('call_rejected',{},room=f'user_{data.get("caller_id")}')
    except: pass

@socketio.on('call_ended')
def on_call_ended(data):
    try:
        emit('call_ended',{},room=f'user_{data.get("other_id")}')
    except: pass

@socketio.on('voice_offer')
def on_voice_offer(data):
    try:
        uid=decode_token(data.get('token',''))['sub']
        emit('voice_offer',{'offer':data.get('offer'),'from':uid},room=f'user_{data.get("target_id")}')
    except: pass

@socketio.on('voice_answer')
def on_voice_answer(data):
    try:
        uid=decode_token(data.get('token',''))['sub']
        emit('voice_answer',{'answer':data.get('answer'),'from':uid},room=f'user_{data.get("target_id")}')
    except: pass

@socketio.on('voice_ice')
def on_voice_ice(data):
    try:
        uid=decode_token(data.get('token',''))['sub']
        emit('voice_ice',{'candidate':data.get('candidate'),'from':uid},room=f'user_{data.get("target_id")}')
    except: pass

@socketio.on('disconnect')
def on_disconnect(): pass

# ── STARTUP ──────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    try:
        migrate_db()
    except Exception as e:
        print(f"Migration warning: {e}")

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
