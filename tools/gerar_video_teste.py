"""Cria um vídeo de teste MJPEG bem diferente do filme: fundo colorido que muda,
um quadrado em movimento e o número da imagem em letras grandes.

Serve para testar fluxos com conteúdo diferente (filme_a com o filme, filme_b com
este) e para ver à primeira vista de que fluxo vem cada janela. Só precisa do
Pillow (python3-pil), que já é preciso para a janela do cliente.

    python3 tools/gerar_video_teste.py /tmp/teste.Mjpeg [IMAGENS]

Formato do ficheiro: cada imagem JPEG é precedida do tamanho em 5 dígitos ASCII.
"""
import io
import sys

from PIL import Image, ImageDraw, ImageFont

LARGURA, ALTURA = 384, 288          # o mesmo tamanho do filme do professor


def cor(i):
    """Cor de fundo que muda devagar: 3 canais com fases diferentes."""
    return tuple(60 + int(120 * abs(((i + fase) % 200) - 100) / 100) for fase in (0, 66, 133))


def imagem(i, total):
    im = Image.new("RGB", (LARGURA, ALTURA), cor(i))
    d = ImageDraw.Draw(im)
    x = int((LARGURA - 60) * abs(((i * 4) % (2 * (LARGURA - 60))) / (LARGURA - 60) - 1))
    d.rectangle((x, 200, x + 60, 260), fill=(255, 255, 255))
    try:
        fonte = ImageFont.load_default(size=72)
    except TypeError:               # Pillow antigo: só tem a letra pequena
        fonte = ImageFont.load_default()
    d.text((20, 40), f"TESTE {i:03d}", fill=(0, 0, 0), font=fonte)
    d.text((20, 140), f"{i}/{total}", fill=(0, 0, 0), font=fonte)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def main(argv):
    if len(argv) not in (2, 3):
        raise SystemExit("uso: gerar_video_teste.py SAIDA.Mjpeg [IMAGENS]")
    total = int(argv[2]) if len(argv) == 3 else 200
    with open(argv[1], "wb") as f:
        for i in range(total):
            dados = imagem(i, total)
            f.write(b"%05d" % len(dados) + dados)
    print(f"{total} imagens escritas em {argv[1]}")


if __name__ == "__main__":
    main(sys.argv)
