# -*- coding: utf-8 -*-
"""C1: lista os uploads RECENTES do canal (roda no venv do drive-to-youtube,
cwd=repo, CHANNEL_ALIAS no env). Saida: linhas __V__<videoId>\t<titulo>."""
import sys

sys.path.insert(0, ".")
from lib import youtube

y = youtube.youtube()
ch = y.channels().list(part="contentDetails", mine=True).execute()["items"][0]
pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
r = y.playlistItems().list(part="snippet", playlistId=pl, maxResults=10).execute()
for it in r.get("items") or []:
    sn = it["snippet"]
    vid = (sn.get("resourceId") or {}).get("videoId", "")
    print("__V__" + vid + "\t" + (sn.get("title") or ""))
