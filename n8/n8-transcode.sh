#!/bin/sh
# Wird von MediaMTX gestartet:  n8-transcode.sh live <kanal>  |  n8-transcode.sh vod <video-id>
# Umgebungsvariablen von MediaMTX: RTSP_PORT, MTX_PATH
# Einstellungen in /etc/default/n8-twitch (optional)
#
# Auf Stabilitaet getrimmt:
#  - gleichmaessige Bildrate (fps-Filter) und Audio-Resampling mit Zeitausgleich gegen Spruenge
#  - konstante Bitrate (CBR, nal-hrd) -> gleichmaessiger Datenstrom fuer den Symbian-Puffer
#  - feste Keyframes alle 2 s, keine Szenenwechsel-Keyframes
#  - kaputte Pakete verwerfen statt abbrechen
KIND="$1"
ID="$2"
[ -f /etc/default/n8-twitch ] && . /etc/default/n8-twitch
SRC_BASE=${N8_SRC_BASE:-https://ttv.serverlele.de}
MODE=${N8_MODE:-transcode}          # transcode | copy
HEIGHT=${N8_HEIGHT:-360}
VBITRATE=${N8_VBITRATE:-600}         # kbit/s, ohne "k"
ABITRATE=${N8_ABITRATE:-64}          # kbit/s
FPS=${N8_FPS:-25}
PRESET=${N8_PRESET:-veryfast}

# alte Schreibweise "700k" in /etc/default/n8-twitch tolerieren
VBITRATE=$(echo "$VBITRATE" | tr -d 'kK')
ABITRATE=$(echo "$ABITRATE" | tr -d 'kK')
GOP=$((FPS * 2))

case "$KIND" in
  vod)  IN="$SRC_BASE/api/vod/$ID.m3u8"; PACE="-re" ;;
  *)    IN="$SRC_BASE/api/channel/hls/$ID.m3u8"; PACE="" ;;
esac
OUT="rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"

INOPTS="-fflags +genpts+discardcorrupt -err_detect ignore_err -rw_timeout 15000000"
MAP="-map 0:v:0 -map 0:a:0 -dn -sn"
AUDIO="-af aresample=async=1:first_pts=0 -c:a aac -ac 2 -ar 44100 -b:a ${ABITRATE}k"

if [ "$MODE" = "copy" ]; then
  exec ffmpeg -hide_banner -loglevel warning $PACE $INOPTS -i "$IN" $MAP \
    -c:v copy $AUDIO \
    -max_muxing_queue_size 1024 -f rtsp -rtsp_transport tcp "$OUT"
fi

exec ffmpeg -hide_banner -loglevel warning $PACE $INOPTS -i "$IN" $MAP \
  -vf "fps=$FPS,scale=-2:$HEIGHT" \
  -c:v libx264 -preset "$PRESET" -tune zerolatency -profile:v baseline -level 3.0 -pix_fmt yuv420p \
  -b:v ${VBITRATE}k -minrate ${VBITRATE}k -maxrate ${VBITRATE}k -bufsize ${VBITRATE}k \
  -x264-params "keyint=$GOP:min-keyint=$GOP:scenecut=0:nal-hrd=cbr:force-cfr=1" \
  $AUDIO \
  -max_muxing_queue_size 1024 -f rtsp -rtsp_transport tcp "$OUT"
