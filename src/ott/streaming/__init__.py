"""Etapa 4 - transmissão de vídeo (UDP) sobre o overlay.

rtp.py     formato dos pacotes: cabeçalho do overlay (origem, fluxo) + RTP
server.py  MjpegFile e StreamSource (emite um fluxo); modo isolado por UDP
client.py  StreamReceiver (recebe, mede perdas/jitter) e janela Tkinter
"""
