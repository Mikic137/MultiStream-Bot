# ⚡ MultiStream Bot - All-in-One Live Streaming Engine

Un bot y suite de overlays **100% local, autónomo y sin cuotas mensuales** para creadores de contenido y streamers multi-plataforma (**Kick.com, Twitch, YouTube Live y TikTok LIVE**).

Reemplaza herramientas fragmentadas y de pago (como TikFinity Pro, Botrix Premium, Streamlabs o StreamElements) ejecutándose directamente en tu propia máquina mediante un servidor web local en Flask y eventos en tiempo real (Server-Sent Events) sincronizados con OBS Studio.

---

## 🚀 Características Principales

* 🌐 **Conexión Multi-Plataforma Simultánea:**
  * **Kick.com:** Chat en vivo, seguidores, suscripciones y canjes de puntos de canal.
  * **Twitch:** Chat IRC en vivo, seguidores, suscripciones y conteo de espectadores.
  * **YouTube Live:** Chat en directo, Super Chats, membresías y viewers concurrentes.
  * **TikTok LIVE:** Comentarios, seguidores, likes, compartidos y regalos (monedas/diamantes).

* 💬 **Chat Multi-Stream (`/chat`):**
  * Unifica todos los comentarios en un solo overlay visual para OBS.
  * Insignias por plataforma, formato dinámico, colores personalizados y modo VIP.

* 👥 **Contador de Viewers Inteligente (`/viewers`):**
  * Pastillas dinámicas con los espectadores de cada plataforma y la suma total.
  * Toggles interactivos en el panel para activar o desactivar plataformas sobre la marcha.
  * Modo *"Ocultar automáticamente si está desconectada/offline"*.

* ⚡ **Sistema de Alertas y Memes (`/alertas`):**
  * Reproductor de videos MP4/WebM y sonidos con animación de entrada y duración individual.
  * Soporta eventos estándar (Follow, Sub, Regalo, Super Chat) y memes personalizados asignados a canjes de puntos o palabras clave.

* 🎙️ **Lector de Comentarios (TTS) y Voces IA (`/voz`):**
  * **Lector TTS:** Edge TTS (voces neuronales en español) o síntesis de Windows.
  * **Modo Exclusivo VIP:** Opción para que **solo lean los VIPs y Moderadores**, evitando spam de texto.
  * **Voces IA Clonadas:** Integración con la API de [Fish Audio](https://fish.audio/) para comandos de chat con personajes (ej. `!b` para Bulma, `!g` para Goku) con cooldown y control de acceso.

* 🎵 **Bot de Música Integrado (`/musica` y `/musica/player`):**
  * Pedidos de canciones mediante `!play <canción o link>` desde el chat.
  * Descarga bajo demanda mediante `yt-dlp` sin requerir FFmpeg pesado.
  * Votación de salto (`!skip`) y visualización de próximas canciones (`!cola`) en OBS.

* 🎡 **Gamificación y Retención:**
  * **Ruleta de Retos (`/overlay`):** Desafíos interactivos en pantalla activados por chat o canjes.
  * **Ruleta de Sorteos (`/sorteo`):** Detección automática de participantes activos del chat y giro aleatorio.
  * **Cuenta Regresiva / Contador (`/contador`):** Temporizador de inicio de stream o contador de victorias.
  * **Meta de Ahorro y Tops (`/meta`, `/tops`):** Barra de objetivos y reconocimiento a mayores donadores.

* 🎛️ **Control de OBS WebSocket (`obs.py`):**
  * Sincronización automática o recarga de fuentes de navegador con un solo clic.

---

## 📋 Requisitos Previos

1. **Sistema Operativo:** Windows 10 u 11 (recomendado) o Linux/macOS.
2. **Python:** Versión **3.10** o superior ([Descargar Python](https://www.python.org/downloads/)).  
   > ⚠️ **Importante:** Al instalar Python en Windows, asegúrate de marcar la casilla **"Add Python to PATH"**.
3. **OBS Studio:** Para proyectar los overlays en tus directos ([Descargar OBS](https://obsproject.com/)).

---

## 🛠️ Instalación y Puesta en Marcha

### 1. Clonar o descargar el repositorio
```bash
git clone https://github.com/tu-usuario/multistream-bot.git
cd multistream-bot
```
*(O simplemente descarga el ZIP desde GitHub y descomprímelo en una carpeta de tu preferencia).*

### 2. Instalar dependencias
Abre una terminal (PowerShell o CMD) en la carpeta del bot y ejecuta:
```bash
pip install -r requirements.txt
```

### 3. Iniciar el bot
Haz doble clic en el archivo **`Iniciar_Bot.bat`** (en Windows) o ejecuta en la consola:
```bash
python app.py
```

El servidor local arrancará en:
👉 **`http://127.0.0.1:5000/`**

Se abrirá automáticamente una ventana limpia con el **Panel de Control** del bot.

---

## ⚙️ Configuración Inicial de Tus Canales

Dentro del Panel de Control (`http://127.0.0.1:5000/`):

1. **Kick:** Escribe el nombre de tu canal (ej: `micanal`) y presiona **Conectar**.
2. **Twitch:** Escribe tu usuario de Twitch (ej: `micanal`) y presiona **Conectar**.
3. **YouTube:** Escribe tu `@handle` o nombre de canal (ej: `@MiCanal`) y presiona **Conectar**.
4. **TikTok LIVE:** Escribe tu usuario con arroba (ej: `@tu_usuario`) y presiona **Conectar**. *(El monitor buscará automáticamente cuando comiences live).*
5. **Plataformas Activas para Viewers:** Marca las casillas de las plataformas que desees mostrar en OBS.

---

## 📺 Integración con OBS Studio (Browser Sources)

En OBS Studio, añade una nueva fuente de tipo **Navegador (Browser Source)** para cada módulo que desees proyectar.

### URLs de los Overlays:

| Módulo OBS | URL Local | Tamaño Recomendado |
| :--- | :--- | :--- |
| **Chat Multi-Stream** | `http://127.0.0.1:5000/chat` | 450 × 600 px |
| **Contador de Viewers** | `http://127.0.0.1:5000/viewers` | 550 × 80 px |
| **Alertas & Memes** | `http://127.0.0.1:5000/alertas` | 1920 × 1080 px |
| **Reproductor de Música** | `http://127.0.0.1:5000/musica/player` | 450 × 200 px |
| **Lector de Voz (Player)** | `http://127.0.0.1:5000/voz/player` | 300 × 150 px *(oculto)* |
| **Ruleta de Retos** | `http://127.0.0.1:5000/overlay` | 600 × 600 px |
| **Ruleta de Sorteos** | `http://127.0.0.1:5000/sorteo` | 800 × 600 px |
| **Cuenta Regresiva / Contador** | `http://127.0.0.1:5000/contador` | 600 × 250 px |
| **Meta de Ahorro** | `http://127.0.0.1:5000/meta` | 500 × 140 px |
| **Top Donadores** | `http://127.0.0.1:5000/top_donador` | 400 × 300 px |

> 💡 **Consejos para OBS:**
> * Activa la opción **«Apagar origen cuando no está visible»** en cada fuente de navegador para ahorrar consumo de CPU.
> * Usa 30 FPS en las fuentes de texto/contadores y 60 FPS en la de Alertas si tus clips son fluidos.

---

## 🎮 Comandos de Chat Disponibles

| Comando | Función | Quién puede usarlo |
| :--- | :--- | :--- |
| `!play <canción / link>` | Añade una canción de YouTube a la cola de música. | Todos |
| `!skip` o `!saltar` | Salta la canción actual. | Streamer (inmediato) / Chat (votos) |
| `!cola` o `!queue` | Muestra el popup en pantalla con las siguientes canciones. | Todos |
| `!reto juega <texto>` | Añade un nuevo reto a la ruleta en pantalla. | Todos |
| `!sorteo` / `!participar` | Registra al usuario en la ruleta de sorteo activa. | Todos |
| `!decir <texto>` | Lee el mensaje mediante voz TTS (en modo comando). | Todos o VIPs |
| `!b <texto>` | Responde con la voz clonada de Bulma (requiere Fish Audio). | Autorizados / VIPs |
| `!g <texto>` | Responde con la voz clonada de Goku (requiere Fish Audio). | Autorizados / VIPs |

---

## 🔑 Configuración de Voces IA (Opcional - Fish Audio)

Si deseas usar las voces clonadas IA:
1. Regístrate en [Fish Audio](https://fish.audio/) y obtén tu API Key gratuita o premium.
2. En el panel del bot, dirígete a la pestaña **Voz (TTS)** -> **Configuración de Voces IA**.
3. Pega tu API Key en el campo correspondiente y guarda los cambios.
4. Puedes añadir tus propios modelos de personajes usando su `model_id`.

---

## 📂 Estructura del Proyecto

```
multistream-bot/
├── app.py                     # Lanzador principal con icono en bandeja de Windows
├── main.py                    # Servidor Flask y orquestador multi-stream
├── live.py                    # Motor de sincronización SSE y dispatcher de eventos
├── kick_client.py             # Cliente Pusher/WebSocket para Kick.com
├── twitch_client.py           # Cliente IRC y API de Twitch
├── youtube_client.py          # Monitor de stream y chat de YouTube Live
├── voz.py                     # Sistema de TTS y voces IA de personajes
├── alertas.py                 # Gestor de alertas, clips de video y sonidos
├── music.py                   # Gestor de cola, descarga yt-dlp y reproducción
├── obs.py                     # Conector OBS WebSocket 5.x
├── vips.py                    # Registro y bienvenida exclusiva de VIPs
├── sorteo.py                  # Ruleta y registro de sorteos
├── templates/                 # Interfaces web y overlays para OBS (HTML5)
├── static/                    # Archivos CSS, Javascript y recursos visuales
├── videos_alertas/            # Carpeta para tus clips MP4 de alertas
├── sonidos_alertas/           # Carpeta para tus efectos de sonido MP3
├── musica/                    # Carpeta temporal para música descargada
└── requirements.txt           # Lista de librerías de Python requeridas
```

---

## 🛡️ Privacidad y Seguridad

* Este software no envía credenciales ni métricas a servidores externos de telemetría.
* Todas las contraseñas, tokens y claves API se guardan únicamente de forma local en tu propia computadora.
* Las peticiones al servidor web están protegidas por cabeceras locales contra ataques de DNS Rebinding.

---

## 📄 Licencia

Distribuido bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más información. Eres libre de modificarlo, adaptarlo y usarlo en tus directos personales o comerciales.
