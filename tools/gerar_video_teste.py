"""Cria um vídeo de teste MJPEG bem diferente do filme: fundo colorido que muda,
um quadrado em movimento, o nome do fluxo e o número da imagem em letras grandes.

Serve para ter fluxos com conteúdo diferente (o filme real num fluxo e estes vídeos nos
outros) e ver à primeira vista de que fluxo vem cada janela do painel. Só precisa do
Pillow (python3-pil), que já é preciso para a janela do cliente.

    python3 tools/gerar_video_teste.py SAIDA.Mjpeg [IMAGENS] [--titulo filme_b] [--matiz 210]

``--matiz`` (0 a 359) escolhe a cor de fundo; sem ele, sai da cor 0 (vermelho).
Os vídeos de ``videos/`` foram feitos com ``tools/gerar_videos_dos_cenarios.sh``.
Formato do ficheiro: cada imagem JPEG é precedida do tamanho em 5 dígitos ASCII.
"""
import argparse
import colorsys
import io

from PIL import Image, ImageDraw, ImageFont

LARGURA, ALTURA = 384, 288          # o mesmo tamanho do filme do professor


def cor(i, matiz=0):
    """Cor de fundo da mesma família (``matiz``) que respira devagar: só muda a luminosidade."""
    v = 0.55 + 0.35 * abs(((i % 100) - 50) / 50)
    r, g, b = colorsys.hsv_to_rgb((matiz % 360) / 360, 0.65, v)
    return int(r * 255), int(g * 255), int(b * 255)


def fonte(tamanho):
    try:
        return ImageFont.load_default(size=tamanho)
    except TypeError:               # Pillow antigo: só tem a letra pequena
        return ImageFont.load_default()


def imagem(i, total, titulo="TESTE", matiz=0):
    im = Image.new("RGB", (LARGURA, ALTURA), cor(i, matiz))
    d = ImageDraw.Draw(im)
    x = int((LARGURA - 60) * abs(((i * 4) % (2 * (LARGURA - 60))) / (LARGURA - 60) - 1))
    d.rectangle((x, 215, x + 60, 270), fill=(255, 255, 255))
    d.text((18, 24), titulo, fill=(255, 255, 255), font=fonte(58), stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((18, 120), f"imagem {i:03d}/{total}", fill=(255, 255, 255), font=fonte(34),
           stroke_width=2, stroke_fill=(0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=65)
    return buf.getvalue()


def escrever(saida, total=100, titulo="TESTE", matiz=0):
    with open(saida, "wb") as f:
        for i in range(total):
            dados = imagem(i, total, titulo, matiz)
            f.write(b"%05d" % len(dados) + dados)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("saida")
    p.add_argument("imagens", nargs="?", type=int, default=100)
    p.add_argument("--titulo", default="TESTE")
    p.add_argument("--matiz", type=int, default=0)
    a = p.parse_args(argv)
    if a.imagens < 1:
        p.error("IMAGENS tem de ser maior que zero")
    escrever(a.saida, a.imagens, a.titulo, a.matiz)
    print(f"{a.imagens} imagens escritas em {a.saida}")


if __name__ == "__main__":
    main()
