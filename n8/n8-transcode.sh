#!/bin/sh
# Wird von MediaMTX gestartet:  n8-transcode.sh live <kanal>  |  n8-transcode.sh vod <video-id>
# Umgebungsvariablen von MediaMTX: RTSP_PORT, MTX_PATH
# Einstellungen in /etc/default/n8-twitch (optional)
KIND="$1"
ID="$2"
[ -f /etc/default/n8-twitch ] && . /etc/default/n8-twitch
SRC_BASE=${N8_SRC_BASE:-https://ttv.serverlele.de}
MODE=${N8_MODE:-transcode}          # transcode | copy
HEIGHT=${N8_HEIGHT:-360}
VBITRATE=${N8_VBITRATE:-700k}

case "$KIND" in
  vod)  IN="$SRC_BASE/api/vod/$ID.m3u8"; PACE="-re" ;;
  *)    IN="$SRC_BASE/api/channel/hls/$ID.m3u8"; PACE="" ;;
esac
OUT="rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"

# Nur Video + Audio: Twitch liefert zusaetzlich ID3-Metadaten, die RTP nicht transportieren kann
MAP="-map 0:v:0 -map 0:a:0 -dn -sn"

if [ "$MODE" = "copy" ]; then
  exec ffmpeg -hide_banner -loglevel warning $PACE -i "$IN" $MAP \
    -c:v copy -c:a aac -ac 2 -ar 44100 -b:a 96k \
    -f rtsp -rtsp_transport tcp "$OUT"
fi

exec ffmpeg -hide_banner -loglevel warning $PACE -i "$IN" $MAP \
  -c:v libx264 -preset veryfast -tune zerolatency -profile:v baseline -level 3.0 \
  -vf "scale=-2:$HEIGHT" -r 25 -b:v "$VBITRATE" -maxrate "$VBITRATE" -bufsize 1400k -g 50 \
  -pix_fmt yuv420p \
  -c:a aac -ac 2 -ar 44100 -b:a 96k \
  -f rtsp -rtsp_transport tcp "$OUT"
