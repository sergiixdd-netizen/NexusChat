# ⚡ NexusChat

Chat en tiempo real con cuentas reales, amigos, servidores y DMs.

## 🚀 Ejecutar en local (Windows)

```bash
cd nexuschat
pip install -r requirements.txt
python app.py
```
Luego abre http://localhost:5000

## 🌐 Subir ONLINE GRATIS (Railway)

1. Ve a https://railway.app y crea cuenta gratuita
2. Haz click en **"New Project" → "Deploy from GitHub"**
3. Sube esta carpeta a un repo de GitHub primero
4. Railway detecta el Procfile automáticamente
5. Tu app estará en una URL pública real

## 🌐 Alternativa: Render (también gratis)

1. Ve a https://render.com
2. New → Web Service → conecta tu repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn --worker-class eventlet -w 1 app:app`

## ✨ Características

- 🔐 Registro y login con contraseña
- 👥 Sistema de amigos real (solicitudes, aceptar/rechazar)
- 💬 Mensajes directos entre usuarios
- 🏠 Servidores con múltiples canales
- 📎 Subida de imágenes y archivos (hasta 500MB)
- 😀 Reacciones con emojis
- ⚡ Mensajes en tiempo real (WebSockets)
- 🔗 Códigos de invitación para servidores
- 🎨 Diseño moderno con efectos de brillo

## Variables de entorno (opcionales para producción)

```
SECRET_KEY=tu_clave_secreta_larga
JWT_SECRET_KEY=otra_clave_secreta
DATABASE_URL=postgresql://... (para usar PostgreSQL en vez de SQLite)
PORT=5000
```
