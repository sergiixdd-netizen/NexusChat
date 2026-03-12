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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','webp','mp4','pdf','txt','zip','mp3','wav','svg'}
MEDIA_EXTENSIONS   = {'png','jpg','jpeg','gif','webp','svg'}
AVATAR_COLORS = ['#7c3aed','#2563eb','#dc2626','#059669','#d97706','#db2777','#0891b2','#ea580c','#65a30d']

def allowed_file(f):
    return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

# ── MODELS ──────────────────────────────────────────────────────────

class User(db.Model):
    id            = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
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
        return {'id': self.id, 'username': self.username,
                'avatar_color': self.avatar_color, 'avatar_url': self.avatar_url,
                'banner_url': self.banner_url, 'bio': self.bio,
                'boost_count': self.boost_count, 'status': self.status}

class Friendship(db.Model):
    id          = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sender_id   = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    status      = db.Column(db.String(20), default='pending')
    created_at  = db.Column(db.DateTime,   default=datetime.utcnow)

class Server(db.Model):
    id          = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    name        = db.Column(db.String(100), nullable=False)
    owner_id    = db.Column(db.String(36),  db.ForeignKey('user.id'), nullable=False)
    invite_code = db.Column(db.String(20),  unique=True, default=lambda: secrets.token_urlsafe(8))
    icon_color  = db.Column(db.String(20),  default='#7c3aed')
    icon_url    = db.Column(db.String(500), nullable=True)
    banner_url  = db.Column(db.String(500), nullable=True)
    description = db.Column(db.String(200), nullable=True)
    boost_count = db.Column(db.Integer,     default=0)
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        mc = ServerMember.query.filter_by(server_id=self.id).count()
        return {'id': self.id, 'name': self.name, 'owner_id': self.owner_id,
                'invite_code': self.invite_code, 'icon_color': self.icon_color,
                'icon_url': self.icon_url, 'banner_url': self.banner_url,
                'description': self.description, 'boost_count': self.boost_count,
                'member_count': mc}

class ServerBoost(db.Model):
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id  = db.Column(db.String(36), db.ForeignKey('server.id'), nullable=False)
    user_id    = db.Column(db.String(36), db.ForeignKey('user.id'),   nullable=False)
    created_at = db.Column(db.DateTime,   default=datetime.utcnow)

class ServerMember(db.Model):
    id        = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id = db.Column(db.String(36), db.ForeignKey('server.id'), nullable=False)
    user_id   = db.Column(db.String(36), db.ForeignKey('user.id'),   nullable=False)
    role      = db.Column(db.String(20), default='member')

class Channel(db.Model):
    id           = db.Column(db.String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    server_id    = db.Column(db.String(36),  db.ForeignKey('server.id'), nullable=False)
    name         = db.Column(db.String(100), nullable=False)
    channel_type = db.Column(db.String(20),  default='text')
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'server_id': self.server_id, 'name': self.name, 'type': self.channel_type}

class Message(db.Model):
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
        return {'id': self.id, 'channel_id': self.channel_id, 'dm_room_id': self.dm_room_id,
                'sender': sender.to_dict() if sender else None,
                'content': self.content, 'file_url': self.file_url,
                'file_name': self.file_name, 'file_type': self.file_type,
                'reactions': reactions, 'created_at': self.created_at.isoformat()}

class Reaction(db.Model):
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id = db.Column(db.String(36), db.ForeignKey('message.id'), nullable=False)
    user_id    = db.Column(db.String(36), db.ForeignKey('user.id'),    nullable=False)
    emoji      = db.Column(db.String(10), nullable=False)

# ── ROUTES ──────────────────────────────────────────────────────────

@app.route('/')
def index(): return render_template('index.html')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Auth
@app.route('/api/register', methods=['POST'])
def register():
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
                avatar_color=random.choice(AVATAR_COLORS))
    db.session.add(user); db.session.commit()
    return jsonify({'token': create_access_token(identity=user.id), 'user': user.to_dict()})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json()
    ident = (d.get('username') or '').strip(); pw = d.get('password') or ''
    user  = User.query.filter((User.username==ident)|(User.email==ident.lower())).first()
    if not user or not bcrypt.check_password_hash(user.password_hash, pw):
        return jsonify({'error':'Usuario o contraseña incorrectos'}),401
    user.status='online'; db.session.commit()
    return jsonify({'token': create_access_token(identity=user.id), 'user': user.to_dict()})

@app.route('/api/me')
@jwt_required()
def get_me():
    u = User.query.get(get_jwt_identity())
    return jsonify(u.to_dict()) if u else (jsonify({'error':'Not found'}),404)

# Profile
@app.route('/api/me/profile', methods=['PATCH'])
@jwt_required()
def update_profile():
    user = User.query.get(get_jwt_identity())
    d = request.get_json()
    if 'bio' in d: user.bio = (d['bio'] or '')[:200]
    db.session.commit(); return jsonify(user.to_dict())

def _save_media(field, prefix):
    if field not in request.files: return None, 'Sin archivo'
    file = request.files[field]
    ext  = (file.filename or '').rsplit('.',1)[-1].lower()
    if ext not in MEDIA_EXTENSIONS: return None, 'Solo imágenes/GIFs'
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

# Friends
@app.route('/api/friends/request', methods=['POST'])
@jwt_required()
def send_friend_request():
    uid = get_jwt_identity()
    tu  = (request.get_json().get('username') or '').strip()
    t   = User.query.filter_by(username=tu).first()
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
    fs  = Friendship.query.filter(
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

# Servers
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
    uid  = get_jwt_identity(); name=(request.get_json().get('name') or '').strip()
    if not name: return jsonify({'error':'Nombre requerido'}),400
    s = Server(name=name,owner_id=uid,icon_color=random.choice(AVATAR_COLORS))
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

@app.route('/api/servers/<sid>/boost', methods=['POST'])
@jwt_required()
def boost_server(sid):
    uid=get_jwt_identity(); user=User.query.get(uid); s=Server.query.get(sid)
    if not s: return jsonify({'error':'Servidor no encontrado'}),404
    if not ServerMember.query.filter_by(server_id=sid,user_id=uid).first():
        return jsonify({'error':'No eres miembro'}),403
    already=ServerBoost.query.filter_by(server_id=sid,user_id=uid).first()
    if already:
        db.session.delete(already); s.boost_count=max(0,s.boost_count-1); user.boost_count+=1
        db.session.commit()
        return jsonify({'boosted':False,'server_boosts':s.boost_count,'user_boosts':user.boost_count})
    if user.boost_count<=0: return jsonify({'error':'No te quedan boosts'}),400
    db.session.add(ServerBoost(server_id=sid,user_id=uid))
    s.boost_count+=1; user.boost_count-=1; db.session.commit()
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
    name=(request.get_json().get('name') or 'nuevo-canal').lower().replace(' ','-')[:40]
    ch=Channel(server_id=sid,name=name); db.session.add(ch); db.session.commit()
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
            d=u.to_dict(); d['role']=m.role; result.append(d)
    return jsonify(result)

# Messages
@app.route('/api/channels/<cid>/messages')
@jwt_required()
def get_channel_messages(cid):
    uid=get_jwt_identity(); ch=Channel.query.get(cid)
    if not ch: return jsonify({'error':'Canal no encontrado'}),404
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
    if not file.filename or not allowed_file(file.filename): return jsonify({'error':'Tipo no permitido'}),400
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

# Sockets
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

@socketio.on('disconnect')
def on_disconnect(): pass
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
