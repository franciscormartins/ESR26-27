#!/bin/sh
# Gera os vídeos distintos de videos/ (um por fluxo dos cenários; o filme_a usa o filme real, progex/movie.Mjpeg).
# Precisa do Pillow: python3 -m venv /tmp/v && /tmp/v/bin/pip install pillow && PY=/tmp/v/bin/python tools/gerar_videos_dos_cenarios.sh
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-python3}"
mkdir -p "$DIR/videos"
"$PY" "$DIR/tools/gerar_video_teste.py" "$DIR/videos/filme_b.Mjpeg" 100 --titulo filme_b --matiz 210
"$PY" "$DIR/tools/gerar_video_teste.py" "$DIR/videos/filme_c.Mjpeg" 100 --titulo filme_c --matiz 130
"$PY" "$DIR/tools/gerar_video_teste.py" "$DIR/videos/filme_d.Mjpeg" 100 --titulo filme_d --matiz 20
