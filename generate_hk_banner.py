"""Generador de Banners y Overlays para Stream de Hollow Knight."""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "static" / "hk_assets"
STATIC_DIR = BASE_DIR / "static"
BRAIN_DIR = Path(r"C:\Users\dylan\.gemini\antigravity\brain\cd57b8d3-a898-4a56-ae4e-9c19552a8223")

# Fuentes del sistema
FONT_TITLE = r"C:\Windows\Fonts\georgiab.ttf"
FONT_TEXT = r"C:\Windows\Fonts\segoeuib.ttf"
FONT_SUB = r"C:\Windows\Fonts\segoeui.ttf"

def get_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

ENEMIES = [
    {
        "id": "enemigos_debiles",
        "file": "aspid_hunter.png",
        "title": "ENEMIGOS DÉBILES",
        "desc": "x3 Áspids Cazadores",
        "cmd_main": "!debil",
        "cmd_sub": "o !enemigo 1",
        "cd": "15 SEGUNDOS",
        "color": (255, 160, 40),
        "bg_glow": (60, 35, 10),
        "scale": 1.4
    },
    {
        "id": "enemigos_medianos",
        "file": "moss_knight.png",
        "title": "ENEMIGOS MEDIANOS",
        "desc": "x2 Caballeros Musgo",
        "cmd_main": "!mediano",
        "cmd_sub": "o !enemigo 2",
        "cd": "30 SEGUNDOS",
        "color": (46, 213, 115),
        "bg_glow": (15, 50, 25),
        "scale": 0.85
    },
    {
        "id": "vasija_pura",
        "file": "pure_vessel.png",
        "title": "VASIJA PURA",
        "desc": "x1 Caballero Sagrado",
        "cmd_main": "!vasija",
        "cmd_sub": "o !enemigo 3",
        "cd": "60 SEGUNDOS",
        "color": (0, 229, 255),
        "bg_glow": (10, 45, 60),
        "scale": 0.72
    },
    {
        "id": "grimm",
        "file": "nightmare_king.png",
        "title": "PESADILLA GRIMM",
        "desc": "x1 Rey de la Pesadilla",
        "cmd_main": "!grimm",
        "cmd_sub": "o !enemigo 4",
        "cd": "60 SEGUNDOS",
        "color": (255, 60, 80),
        "bg_glow": (60, 15, 25),
        "scale": 0.52
    },
    {
        "id": "destello_absoluto",
        "file": "radiance.png",
        "title": "DESTELLO ABSOLUTO",
        "desc": "x1 Dios de la Luz",
        "cmd_main": "!destello",
        "cmd_sub": "o !enemigo 5",
        "cd": "90 SEGUNDOS",
        "color": (255, 215, 0),
        "bg_glow": (65, 50, 10),
        "scale": 0.55
    }
]

def draw_rounded_rect(draw, bbox, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=width)

def generar_banner_completo():
    """Genera banner 1920x1080 Full HD."""
    W, H = 1920, 1080
    im = Image.new("RGBA", (W, H), (10, 14, 23, 255))
    draw = ImageDraw.Draw(im)

    # Fondo degradado radial simulado / partículas
    for y in range(H):
        ratio = y / H
        r = int(9 + 10 * (1 - ratio))
        g = int(14 + 14 * (1 - ratio))
        b = int(26 + 28 * (1 - ratio))
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # Título Principal
    font_header = get_font(FONT_TITLE, 46)
    font_sub_h = get_font(FONT_TEXT, 22)
    font_tag = get_font(FONT_TEXT, 18)

    title_text = "⚔️ ¡INVOCA ENEMIGOS DESDE EL CHAT! ⚔️"
    sub_title = "Escribe el comando en TikTok Live para que aparezcan en mi partida en tiempo real"
    tag_live = "● EN VIVO EN TIKTOK"

    # Badge superior LIVE
    draw_rounded_rect(draw, (W//2 - 120, 28, W//2 + 120, 60), radius=16, fill=(255, 40, 80, 220))
    draw.text((W//2, 44), tag_live, fill=(255, 255, 255), font=font_tag, anchor="mm")

    draw.text((W//2, 95), title_text, fill=(240, 246, 255), font=font_header, anchor="mm")
    draw.text((W//2, 142), sub_title, fill=(160, 180, 210), font=font_sub_h, anchor="mm")

    # Línea divisoria elegante
    draw.line([(200, 175), (W - 200, 175)], fill=(50, 70, 100), width=2)
    draw.line([(W//2 - 150, 175), (W//2 + 150, 175)], fill=(0, 229, 255), width=3)

    # 5 Tarjetas
    card_w = 340
    card_h = 750
    gap = 24
    start_x = (W - (card_w * 5 + gap * 4)) // 2
    top_y = 210

    font_card_title = get_font(FONT_TITLE, 21)
    font_card_desc = get_font(FONT_SUB, 16)
    font_cmd_main = get_font(FONT_TEXT, 30)
    font_cmd_sub = get_font(FONT_SUB, 17)
    font_cd_label = get_font(FONT_SUB, 14)
    font_cd_val = get_font(FONT_TEXT, 18)

    for i, enemy in enumerate(ENEMIES):
        cx = start_x + i * (card_w + gap)
        cy = top_y
        col = enemy["color"]
        glow = enemy["bg_glow"]

        # Sombra y fondo de tarjeta
        draw_rounded_rect(draw, (cx, cy, cx + card_w, cy + card_h), radius=22, fill=(15, 21, 33, 240), outline=(col[0]//2, col[1]//2, col[2]//2), width=2)

        # Resplandor sutil superior de tarjeta
        draw_rounded_rect(draw, (cx + 4, cy + 4, cx + card_w - 4, cy + 180), radius=18, fill=(glow[0], glow[1], glow[2], 160))

        # Cargar sprite
        sprite_path = ASSETS_DIR / enemy["file"]
        if sprite_path.exists():
            sp = Image.open(sprite_path).convert("RGBA")
            # Escalar sprite proporcionalmente
            scale = enemy["scale"]
            sw = int(sp.width * scale)
            sh = int(sp.height * scale)
            # Limitar dentro del cuadro de 300x260
            max_w, max_h = 280, 250
            if sw > max_w or sh > max_h:
                ratio = min(max_w / sw, max_h / sh)
                sw = int(sw * ratio)
                sh = int(sh * ratio)
            sp = sp.resize((sw, sh), Image.Resampling.LANCZOS)

            # Centrar en la zona de arte
            art_cx = cx + card_w // 2
            art_cy = cy + 160
            im.paste(sp, (art_cx - sw // 2, art_cy - sh // 2), sp)

        # Nombre del enemigo
        draw.text((cx + card_w // 2, cy + 315), enemy["title"], fill=col, font=font_card_title, anchor="mm")
        draw.text((cx + card_w // 2, cy + 345), enemy["desc"], fill=(180, 195, 215), font=font_card_desc, anchor="mm")

        # Divisor interno
        draw.line([(cx + 40, cy + 375), (cx + card_w - 40, cy + 375)], fill=(40, 55, 75), width=1)

        # Caja del COMANDO PRINCIPAL
        cmd_box_top = cy + 400
        cmd_box_h = 95
        draw_rounded_rect(draw, (cx + 25, cmd_box_top, cx + card_w - 25, cmd_box_top + cmd_box_h), radius=14, fill=(8, 12, 20), outline=col, width=2)

        draw.text((cx + card_w // 2, cmd_box_top + 32), enemy["cmd_main"], fill=(255, 255, 255), font=font_cmd_main, anchor="mm")
        draw.text((cx + card_w // 2, cmd_box_top + 68), enemy["cmd_sub"], fill=(150, 170, 200), font=font_cmd_sub, anchor="mm")

        # Etiqueta "Escribe esto en el chat"
        draw.text((cx + card_w // 2, cmd_box_top - 14), "COMANDO EN CHAT", fill=col, font=font_cd_label, anchor="mm")

        # Caja de COOLDOWN / RECARGA
        cd_box_top = cy + 540
        cd_box_h = 80
        draw_rounded_rect(draw, (cx + 35, cd_box_top, cx + card_w - 35, cd_box_top + cd_box_h), radius=14, fill=(22, 28, 42), outline=(50, 70, 95), width=1)

        draw.text((cx + card_w // 2, cd_box_top + 26), "TIEMPO DE RECARGA", fill=(140, 160, 190), font=font_cd_label, anchor="mm")
        draw.text((cx + card_w // 2, cd_box_top + 54), f"⏳ {enemy['cd']}", fill=col, font=font_cd_val, anchor="mm")

        # Pie de tarjeta con indicador de modo
        draw.text((cx + card_w // 2, cy + 690), "Cooldown Independiente", fill=(100, 120, 145), font=font_cd_label, anchor="mm")

    # Pie general del banner
    footer_text = "⚡ Cada jefe y enemigo se recarga por separado. ¡Combina comandos y prueba la habilidad del streamer!"
    draw.text((W//2, 1020), footer_text, fill=(160, 185, 215), font=font_sub_h, anchor="mm")

    # Guardar
    out_static = STATIC_DIR / "hk_stream_commands.png"
    out_brain = BRAIN_DIR / "hk_stream_commands.png"
    im.save(out_static, "PNG")
    im.save(out_brain, "PNG")
    print(f"[Banner] Creado {out_static} ({W}x{H})")
    return out_brain

def generar_banner_horizontal():
    """Genera barra horizontal compacta 1920x360 para overlay de OBS."""
    W, H = 1920, 360
    im = Image.new("RGBA", (W, H), (10, 14, 23, 245))
    draw = ImageDraw.Draw(im)

    # Borde superior e inferior iluminado
    draw.line([(0, 0), (W, 0)], fill=(0, 229, 255), width=3)
    draw.line([(0, H-1), (W, H-1)], fill=(40, 60, 90), width=2)

    font_title = get_font(FONT_TITLE, 24)
    font_sub_h = get_font(FONT_SUB, 15)
    font_cmd = get_font(FONT_TEXT, 24)
    font_label = get_font(FONT_SUB, 13)
    font_cd = get_font(FONT_TEXT, 15)

    # Cabecera fina superior
    draw.text((40, 28), "⚔️ HOLLOW KNIGHT INTERACTIVO - ¡COMANDOS EN EL CHAT DE TIKTOK!", fill=(0, 229, 255), font=font_title, anchor="lm")
    draw.text((W - 40, 28), "● CADA ENEMIGO TIENE SU PROPIO COOLDOWN", fill=(255, 60, 80), font=font_cd, anchor="rm")
    draw.line([(40, 48), (W - 40, 48)], fill=(30, 45, 65), width=1)

    card_w = 350
    card_h = 280
    gap = 20
    start_x = (W - (card_w * 5 + gap * 4)) // 2
    top_y = 62

    for i, enemy in enumerate(ENEMIES):
        cx = start_x + i * (card_w + gap)
        cy = top_y
        col = enemy["color"]

        # Tarjeta
        draw_rounded_rect(draw, (cx, cy, cx + card_w, cy + card_h), radius=14, fill=(15, 21, 33, 240), outline=col, width=1)

        # Sprite a la izquierda
        sprite_path = ASSETS_DIR / enemy["file"]
        if sprite_path.exists():
            sp = Image.open(sprite_path).convert("RGBA")
            scale = enemy["scale"] * 0.45
            sw = int(sp.width * scale)
            sh = int(sp.height * scale)
            max_s = 90
            if sw > max_s or sh > max_s:
                ratio = min(max_s / sw, max_s / sh)
                sw = int(sw * ratio)
                sh = int(sh * ratio)
            sp = sp.resize((sw, sh), Image.Resampling.LANCZOS)
            im.paste(sp, (cx + 15 + (90 - sw)//2, cy + 40 + (90 - sh)//2), sp)

        # Info a la derecha del sprite
        text_x = cx + 125
        draw.text((text_x, cy + 30), enemy["title"][:16], fill=col, font=font_cd, anchor="lm")
        draw.text((text_x, cy + 50), enemy["desc"], fill=(160, 180, 205), font=font_label, anchor="lm")

        # Caja comando
        draw_rounded_rect(draw, (cx + 15, cy + 145, cx + card_w - 15, cy + 205), radius=10, fill=(8, 12, 20), outline=col, width=1)
        draw.text((cx + card_w // 2, cy + 175), f"{enemy['cmd_main']}  ({enemy['cmd_sub']})", fill=(255, 255, 255), font=font_cmd, anchor="mm")

        # Cooldown pill
        draw_rounded_rect(draw, (cx + 25, cy + 220, cx + card_w - 25, cy + 260), radius=8, fill=(22, 28, 42))
        draw.text((cx + card_w // 2, cy + 240), f"⏳ Recarga: {enemy['cd']}", fill=col, font=font_cd, anchor="mm")

    # Guardar
    out_static = STATIC_DIR / "hk_stream_commands_horizontal.png"
    out_brain = BRAIN_DIR / "hk_stream_commands_horizontal.png"
    im.save(out_static, "PNG")
    im.save(out_brain, "PNG")
    print(f"[Banner Horizontal] Creado {out_static} ({W}x{H})")
    return out_brain

if __name__ == "__main__":
    generar_banner_completo()
    generar_banner_horizontal()
