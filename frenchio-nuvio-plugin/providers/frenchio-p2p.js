// LeFlux. P2P — Nuvio Provider Plugin v1.0.0
// Built for Hermes (React Native)
// Do not edit — edit src/frenchio-p2p/index.js and run: npm run build


// src/frenchio-p2p/index.js
function getSettings() {
  var s = {};
  if (typeof globalThis !== "undefined") {
    if (globalThis.SCRAPER_SETTINGS) {
      if (globalThis.SCRAPER_SETTINGS.config_b64) {
        try {
          var decoded = atob(globalThis.SCRAPER_SETTINGS.config_b64);
          s = JSON.parse(decoded) || {};
        } catch (e) {
          console.log("[Frenchio P2P] Failed to decode config_b64:", e.message);
        }
      } else {
        s = globalThis.SCRAPER_SETTINGS;
      }
    } else if (globalThis._settings) {
      s = globalThis._settings;
    }
  }
  return {
    c411_apikey: s.c411_apikey || "",
    c411_passkey: s.c411_passkey || "",
    torr9_passkey: s.torr9_passkey || "",
    tr4ker_apikey: s.tr4ker_apikey || "",
    tr4ker_passkey: s.tr4ker_passkey || "",
    gemini_apikey: s.gemini_apikey || "",
    gemini_passkey: s.gemini_passkey || "",
    max_size_gb: parseFloat(s.max_size_gb) || 50,
    proxy_base: s.proxy_base || ""
  };
}
var C411_BASE = "https://c411.org/api";
var TORR9_BASE = "https://api.torr9.net/api/v1/torznab";
var TR4KER_BASE = "https://tr4ker.net/torznab";
var GEMINI_BASE = "https://gemini-tracker.org/api/torrents/filter";
function safeFetch(url, opts) {
  var proxyBase = getSettings().proxy_base;
  var finalUrl = url;
  if (proxyBase) {
    finalUrl = proxyBase + "?url=" + encodeURIComponent(url);
  }
  return fetch(finalUrl, opts || {}).then(function(r) {
    if (!r.ok)
      throw new Error("HTTP " + r.status);
    return r;
  });
}
function gbToBytes(gb) {
  return gb * 1024 * 1024 * 1024;
}
function sizeLabel(bytes) {
  if (!bytes)
    return "";
  var gb = bytes / 1024 / 1024 / 1024;
  return gb >= 1 ? gb.toFixed(2) + " GB" : (bytes / 1024 / 1024).toFixed(0) + " MB";
}
function qualityFromName(name) {
  var n = name.toUpperCase();
  if (n.includes("2160") || n.includes("4K") || n.includes("UHD"))
    return "4K";
  if (n.includes("1080"))
    return "1080p";
  if (n.includes("720"))
    return "720p";
  if (n.includes("576"))
    return "576p";
  if (n.includes("480"))
    return "480p";
  return "SD";
}
function buildMagnet(hash, name, trackers) {
  var dn = encodeURIComponent(name);
  var base = "magnet:?xt=urn:btih:" + hash + "&dn=" + dn;
  var trs = (trackers || []).map(function(t) {
    return "&tr=" + encodeURIComponent(t);
  }).join("");
  return base + trs;
}
var OPEN_TRACKERS = [
  "udp://tracker.openbittorrent.com:6969/announce",
  "udp://tracker.opentrackr.org:1337/announce",
  "udp://open.stealth.si:80/announce"
];
function getStreamUrl(torrent, settings) {
  var hash = (torrent.info_hash || torrent.hash || "").toLowerCase();
  var name = torrent.name || torrent.title || "Inconnu";
  var source = torrent.source || "";
  var announceUrl = null;
  if (source === "torr9" && settings.torr9_passkey) {
    announceUrl = "http://tracker.torr9.net/announce/" + settings.torr9_passkey;
  } else if (source === "gemini") {
    if (settings.gemini_passkey) {
      announceUrl = "http://gemini-tracker.org/announce/" + settings.gemini_passkey;
    }
  } else if (source === "c411" && settings.c411_passkey) {
    announceUrl = "http://c411.org/announce/" + settings.c411_passkey;
  } else if (source === "tr4ker" && settings.tr4ker_passkey) {
    announceUrl = "http://tk.tr4ker.net/announce/" + settings.tr4ker_passkey;
  }
  var trackers = announceUrl ? [announceUrl].concat(OPEN_TRACKERS) : OPEN_TRACKERS;
  return buildMagnet(hash, name, trackers);
}
function formatStreamCard(torrent, trackerName) {
  var rawName = torrent.name || torrent.title || "Inconnu";
  var rawNameLower = rawName.toLowerCase();
  var q = "";
  if (rawNameLower.indexOf("2160") !== -1 || rawNameLower.indexOf("4k") !== -1 || rawNameLower.indexOf("uhd") !== -1) {
    q = "4K";
  } else if (rawNameLower.indexOf("1080") !== -1 || rawNameLower.indexOf("fhd") !== -1) {
    q = "1080p";
  } else if (rawNameLower.indexOf("720") !== -1 || rawNameLower.indexOf("hd") !== -1) {
    q = "720p";
  } else if (rawNameLower.indexOf("576") !== -1) {
    q = "576p";
  } else if (rawNameLower.indexOf("480") !== -1) {
    q = "480p";
  } else {
    q = "SD";
  }
  var releaseBadge = "";
  if (/\bremux\b/.test(rawNameLower)) {
    releaseBadge = "Remux";
  } else if (/blu[-_. ]?ray|bluray/.test(rawNameLower) && !/\bremux\b/.test(rawNameLower)) {
    if (/\bbdrip\b/.test(rawNameLower)) {
      releaseBadge = "BDRip";
    } else {
      releaseBadge = "BluRay";
    }
  } else if (/web[-_. ]?dl|webdl/.test(rawNameLower)) {
    releaseBadge = "WEB-DL";
  } else if (/web[-_. ]?rip|webrip/.test(rawNameLower)) {
    releaseBadge = "WEBRip";
  } else if (/\bhdtv\b/.test(rawNameLower)) {
    releaseBadge = "HDTV";
  }
  var hdrBadge = "";
  if (/\bdv\b|dolby.?vision/.test(rawNameLower)) {
    hdrBadge = "DV";
  } else if (/hdr10\+|hdr10plus/.test(rawNameLower)) {
    hdrBadge = "HDR10+";
  } else if (/\bhdr\b/.test(rawNameLower)) {
    hdrBadge = "HDR";
  } else if (/\bsdr\b/.test(rawNameLower)) {
    hdrBadge = "SDR";
  }
  var codecBadge = "";
  if (/\bx265\b|hevc/.test(rawNameLower)) {
    codecBadge = "HEVC";
  } else if (/\bx264\b|\bavc\b/.test(rawNameLower)) {
    codecBadge = "AVC";
  } else if (/\bav1\b/.test(rawNameLower)) {
    codecBadge = "AV1";
  }
  var audioBadge = "";
  if (/truehd|atmos/.test(rawNameLower)) {
    audioBadge = "TrueHD";
  } else if (/dts[-_. ]?hd|dts[-_. ]?ma/.test(rawNameLower)) {
    audioBadge = "DTS-HD";
  } else if (/\bdts\b/.test(rawNameLower)) {
    audioBadge = "DTS";
  } else if (/dd\+|eac3|e-ac-3/.test(rawNameLower)) {
    audioBadge = "DD+";
  } else if (/\bac3\b|dolby.?digital/.test(rawNameLower)) {
    audioBadge = "DD";
  } else if (/\baac\b/.test(rawNameLower)) {
    audioBadge = "AAC";
  } else if (/\bflac\b/.test(rawNameLower)) {
    audioBadge = "FLAC";
  }
  var isMulti = rawNameLower.indexOf("multi") !== -1 || rawNameLower.indexOf("vf+vo") !== -1 || rawNameLower.indexOf("vo+vf") !== -1;
  var isTrueFrench = rawNameLower.indexOf("truefrench") !== -1 || rawNameLower.indexOf("vff") !== -1;
  var isVostfr = rawNameLower.indexOf("vostfr") !== -1 || rawNameLower.indexOf("subfrench") !== -1 || rawNameLower.indexOf("vosfr") !== -1;
  var isVf = /\bvf\b|\bfrench\b/.test(rawNameLower);
  var langBadge = "";
  if (isMulti) {
    langBadge = "MULTI";
  } else if (isTrueFrench) {
    langBadge = "TrueFrench";
  } else if (isVostfr) {
    langBadge = "VOSTFR";
  } else if (isVf) {
    langBadge = "VF";
  }
  var size = torrent.size || torrent.file_size || 0;
  var szLabel = sizeLabel(size);
  var nameParts = [];
  if (q)
    nameParts.push(q);
  if (szLabel)
    nameParts.push(szLabel);
  if (langBadge)
    nameParts.push(langBadge);
  nameParts.push(trackerName);
  var formattedName = nameParts.join(" \xB7 ");
  var descParts = [];
  if (releaseBadge)
    descParts.push(releaseBadge);
  if (codecBadge)
    descParts.push(codecBadge);
  if (hdrBadge)
    descParts.push(hdrBadge);
  if (audioBadge)
    descParts.push(audioBadge);
  var formattedDesc = descParts.join(" \xB7 ");
  return {
    formattedName,
    formattedDesc,
    quality: q,
    sizeLabel: szLabel
  };
}
function normalize(str) {
  return (str || "").toLowerCase().replace(/[éèêë]/g, "e").replace(/[àâä]/g, "a").replace(/[ùûü]/g, "u").replace(/[ôö]/g, "o").replace(/[îï]/g, "i").replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}
function titleMatch(torrentName, expectedTitle) {
  var n = normalize(torrentName);
  var t = normalize(expectedTitle);
  return n.includes(t) || t.split(" ").every(function(w) {
    return w.length < 3 || n.includes(w);
  });
}
function seasonEpisodeMatch(torrentName, season, episode) {
  if (!season)
    return true;
  var n = torrentName.toUpperCase();
  var s = String(season).padStart(2, "0");
  var e = episode ? String(episode).padStart(2, "0") : null;
  var hasS = n.includes("S" + s);
  if (!hasS)
    return false;
  if (!e)
    return true;
  return n.includes("S" + s + "E" + e) || n.includes("E" + e);
}
function estimateEpisodeSize(torrentName, totalSize) {
  if (!totalSize)
    return 0;
  var nameLower = torrentName.toLowerCase();
  var seasonRange = nameLower.match(/s(\d+)\s*-\s*s?(\d+)/);
  if (seasonRange) {
    try {
      var sStart = parseInt(seasonRange[1], 10);
      var sEnd = parseInt(seasonRange[2], 10);
      var numSeasons = Math.max(1, sEnd - sStart + 1);
      var numEpisodes = numSeasons * 10;
      return Math.floor(totalSize / numEpisodes);
    } catch (e) {
    }
  }
  if (nameLower.indexOf("integrale") !== -1 || nameLower.indexOf("complete") !== -1 || nameLower.indexOf("pack") !== -1) {
    return Math.floor(totalSize / 20);
  }
  var hasSeason = /s\d+|season\s*\d+|saison\s*\d+/.test(nameLower);
  var hasEpisode = /e\d+|ep\d+|episode\s*\d+|\d+x\d+|\bx\d{1,2}\b/.test(nameLower);
  if (hasSeason && !hasEpisode) {
    return Math.floor(totalSize / 10);
  }
  return totalSize;
}
function formatStream(torrent, trackerName, streamUrl) {
  var card = formatStreamCard(torrent, trackerName);
  var rawName = torrent.name || torrent.title || "Inconnu";
  var desc = card.formattedDesc || rawName;
  var size = torrent.size || 0;
  var maxSz = gbToBytes(getSettings().max_size_gb);
  if (maxSz > 0 && size > maxSz)
    return null;
  var hash = (torrent.info_hash || torrent.hash || "").toLowerCase();
  var settings = getSettings();
  var announceUrl = null;
  var source = torrent.source || "";
  if (source === "torr9" && settings.torr9_passkey) {
    announceUrl = "http://tracker.torr9.net/announce/" + settings.torr9_passkey;
  } else if (source === "gemini") {
    if (settings.gemini_passkey) {
      announceUrl = "http://gemini-tracker.org/announce/" + settings.gemini_passkey;
    }
  } else if (source === "c411" && settings.c411_passkey) {
    announceUrl = "http://c411.org/announce/" + settings.c411_passkey;
  } else if (source === "tr4ker" && settings.tr4ker_passkey) {
    announceUrl = "http://tk.tr4ker.net/announce/" + settings.tr4ker_passkey;
  }
  var trackers = announceUrl ? [announceUrl].concat(OPEN_TRACKERS) : OPEN_TRACKERS;
  return {
    name: card.formattedName,
    title: desc,
    description: desc,
    url: streamUrl,
    infoHash: hash,
    sources: trackers.map(function(t) {
      return "tracker:" + t;
    }),
    quality: card.quality,
    size: card.sizeLabel || "Unknown",
    provider: "leflux-p2p."
  };
}
function resolveMediaInfoFromTmdb(tmdbId, mediaType) {
  var tmdbKey = typeof globalThis !== "undefined" && globalThis._tmdbApiKey || "1865f43a0549ca50d341dd9ab8b29f49";
  var endpoint = mediaType === "movie" ? "movie" : "tv";
  var url = "https://api.themoviedb.org/3/" + endpoint + "/" + tmdbId + "?api_key=" + tmdbKey + "&append_to_response=external_ids";
  return safeFetch(url).then(function(r) {
    return r.json();
  }).then(function(d) {
    var title = d.title || d.name || null;
    var imdbId = d.external_ids && d.external_ids.imdb_id ? d.external_ids.imdb_id : null;
    var year = null;
    var dateStr = d.release_date || d.first_air_date;
    if (dateStr) {
      var parts = dateStr.split("-");
      if (parts[0])
        year = parseInt(parts[0]) || null;
    }
    return { title, imdbId, year };
  }).catch(function() {
    return { title: null, imdbId: null, year: null };
  });
}
function flattenResults(results) {
  var all = [];
  results.forEach(function(list) {
    all = all.concat(list);
  });
  return all;
}
function queryC411(mediaInfo, tmdbId, mediaType, season, episode) {
  var apikey = getSettings().c411_apikey;
  if (!apikey)
    return Promise.resolve([]);
  var searchUnit = function(ep) {
    var params = new URLSearchParams({
      apikey,
      o: "json"
    });
    if (mediaType === "movie") {
      params.set("t", "movie");
      if (mediaInfo.imdbId) {
        params.set("imdbid", mediaInfo.imdbId);
      } else {
        params.set("q", mediaInfo.title);
      }
    } else {
      params.set("t", "tvsearch");
      if (mediaInfo.imdbId) {
        params.set("imdbid", mediaInfo.imdbId);
      } else {
        params.set("tmdbid", tmdbId);
      }
      if (season !== null && season !== void 0)
        params.set("season", season);
      if (ep !== null && ep !== void 0)
        params.set("episode", ep);
    }
    var url = C411_BASE + "?" + params.toString();
    return safeFetch(url).then(function(r) {
      return r.json();
    }).then(function(data) {
      var channel = data && data.channel ? data.channel : {};
      var items = channel.item || [];
      if (!Array.isArray(items))
        items = [items];
      return items.map(function(item) {
        if (!item || !item.title)
          return null;
        var attrs = item["torznab:attr"] || [];
        if (!Array.isArray(attrs))
          attrs = [attrs];
        var info_hash = null;
        var seeders = 0;
        for (var i = 0; i < attrs.length; i++) {
          var attr = attrs[i] && attrs[i]["@attributes"] ? attrs[i]["@attributes"] : {};
          if (attr.name === "infohash")
            info_hash = attr.value;
          if (attr.name === "seeders")
            seeders = parseInt(attr.value) || 0;
        }
        if (!info_hash)
          info_hash = item.guid;
        var enclosure = item.enclosure && item.enclosure["@attributes"] ? item.enclosure["@attributes"] : {};
        var download_link = enclosure.url || "";
        return {
          info_hash,
          name: item.title,
          size: parseInt(item.size) || 0,
          seeders,
          source: "c411",
          link: download_link
        };
      }).filter(Boolean);
    }).catch(function(e) {
      console.log("[LeFlux. P2P] C411 error:", e.message);
      return [];
    });
  };
  var searches = [searchUnit(episode)];
  if (mediaType === "tv" && season !== null && season !== void 0 && episode !== null && episode !== void 0) {
    searches.push(searchUnit(null));
  }
  return Promise.all(searches).then(flattenResults);
}
function queryTorr9(mediaInfo, tmdbId, mediaType, season, episode) {
  var passkey = getSettings().torr9_passkey;
  if (!passkey)
    return Promise.resolve([]);
  var searchUnit = function(ep) {
    var params = new URLSearchParams({
      apikey: passkey
    });
    if (mediaType === "movie") {
      params.set("t", "movie");
      if (mediaInfo.imdbId) {
        params.set("imdbid", mediaInfo.imdbId);
      } else {
        params.set("q", mediaInfo.title);
      }
    } else {
      params.set("t", "tvsearch");
      if (mediaInfo.imdbId) {
        params.set("imdbid", mediaInfo.imdbId);
      } else {
        params.set("tmdbid", tmdbId);
      }
      if (season !== null && season !== void 0)
        params.set("season", season);
      if (ep !== null && ep !== void 0)
        params.set("episode", ep);
    }
    var url = TORR9_BASE + "?" + params.toString();
    return safeFetch(url).then(function(r) {
      return r.text();
    }).then(function(xml) {
      var items = parseTorznabItems(xml);
      var filtered = items;
      if (mediaInfo.title) {
        filtered = items.filter(function(t) {
          return titleMatch(t.name, mediaInfo.title);
        });
      }
      if (mediaType === "tv" && season) {
        filtered = filtered.filter(function(t) {
          return seasonEpisodeMatch(t.name, season, ep);
        });
      }
      return filtered.map(function(t) {
        return {
          info_hash: t.info_hash,
          name: t.name,
          size: t.size,
          seeders: t.seeders,
          source: "torr9",
          link: t.link
        };
      });
    }).catch(function(e) {
      console.log("[LeFlux. P2P] Torr9 error:", e.message);
      return [];
    });
  };
  var searches = [searchUnit(episode)];
  if (mediaType === "tv" && season !== null && season !== void 0 && episode !== null && episode !== void 0) {
    searches.push(searchUnit(null));
  }
  return Promise.all(searches).then(flattenResults);
}
function queryGemini(mediaInfo, tmdbId, mediaType, season, episode) {
  var apikey = getSettings().gemini_apikey;
  if (!apikey)
    return Promise.resolve([]);
  var searchUnit = function(ep) {
    var params = new URLSearchParams({
      api_token: apikey
    });
    if (mediaInfo.imdbId) {
      params.set("imdbId", mediaInfo.imdbId.replace("tt", ""));
    } else {
      params.set("tmdbId", tmdbId);
    }
    if (mediaType === "tv" && season !== null && season !== void 0) {
      params.set("seasonNumber", season);
      if (ep !== null && ep !== void 0) {
        params.set("episodeNumber", ep);
      }
    }
    var url = GEMINI_BASE + "?" + params.toString();
    return safeFetch(url).then(function(r) {
      return r.json();
    }).then(function(data) {
      var torrents = data && data.data ? data.data : Array.isArray(data) ? data : [];
      return torrents.map(function(t) {
        var item = t;
        if (t && t.attributes) {
          item = {};
          for (var k in t) {
            if (k !== "attributes")
              item[k] = t[k];
          }
          for (var attr in t.attributes) {
            item[attr] = t.attributes[attr];
          }
        }
        return {
          info_hash: item.info_hash || item.hash || "",
          name: item.name || item.title || "Inconnu",
          size: item.size || item.file_size || 0,
          seeders: item.seeders || item.seeds || 0,
          source: "gemini",
          link: item.download_link || item.link || ""
        };
      });
    }).catch(function(e) {
      console.log("[LeFlux. P2P] Gemini error:", e.message);
      return [];
    });
  };
  var searches = [searchUnit(episode)];
  if (mediaType === "tv" && season !== null && season !== void 0 && episode !== null && episode !== void 0) {
    searches.push(searchUnit(null));
  }
  return Promise.all(searches).then(flattenResults);
}
function parseTorznabItems(xml) {
  var items = [];
  var itemRe = /<item>([\s\S]*?)<\/item>/gi;
  var m;
  while ((m = itemRe.exec(xml)) !== null) {
    var block = m[1];
    var get = function(tag) {
      var re2 = new RegExp("<" + tag + "[^>]*>([^<]*)</" + tag + ">", "i");
      var m2 = block.match(re2);
      return m2 ? m2[1].trim() : "";
    };
    var getAttr = function(tag, attr) {
      var re3 = new RegExp("<" + tag + "[^>]+" + attr + '="([^"]*)"', "i");
      var m3 = block.match(re3);
      return m3 ? m3[1] : "";
    };
    var hash = getAttr('torznab:attr[name="infohash"]', "value") || getAttr("torznab:attr", "value");
    var infohashMatch = block.match(/name="infohash"\s+value="([^"]+)"/i);
    if (infohashMatch)
      hash = infohashMatch[1];
    var sizeStr = get("size") || getAttr("enclosure", "length");
    var seeders = 0;
    var seedersMatch = block.match(/name="seeders"\s+value="([^"]+)"/i);
    if (seedersMatch)
      seeders = parseInt(seedersMatch[1]) || 0;
    items.push({
      name: get("title"),
      info_hash: hash,
      size: parseInt(sizeStr) || 0,
      seeders,
      link: getAttr("enclosure", "url")
    });
  }
  return items;
}
function searchTR4KER(title, mediaType, season, episode) {
  var apikey = getSettings().tr4ker_apikey;
  if (!apikey || !title)
    return Promise.resolve([]);
  var query = title;
  if (season)
    query += " S" + String(season).padStart(2, "0");
  if (episode)
    query += "E" + String(episode).padStart(2, "0");
  var params = new URLSearchParams({
    t: "search",
    apikey,
    q: query,
    cat: mediaType === "movie" ? "2000" : "5000",
    limit: 50
  });
  var url = TR4KER_BASE + "?" + params.toString();
  return safeFetch(url).then(function(r) {
    return r.text();
  }).then(function(xml) {
    var items = parseTorznabItems(xml);
    return items.filter(function(t) {
      return titleMatch(t.name, title);
    }).filter(function(t) {
      return seasonEpisodeMatch(t.name, season, episode);
    }).map(function(t) {
      return {
        info_hash: t.info_hash,
        name: t.name,
        size: t.size,
        seeders: t.seeders,
        source: "tr4ker",
        link: t.link
      };
    });
  }).catch(function(e) {
    console.log("[LeFlux. P2P] TR4KER error:", e.message);
    return [];
  });
}
function dedup(torrents) {
  var seen = {};
  return torrents.filter(function(t) {
    if (!t || !t.info_hash)
      return false;
    var key = t.info_hash.toLowerCase();
    if (seen[key])
      return false;
    seen[key] = true;
    return true;
  });
}
var QUALITY_ORDER = { "4K": 0, "1080p": 1, "720p": 2, "576p": 3, "480p": 4, "SD": 5 };
function sortStreams(torrents) {
  return torrents.sort(function(a, b) {
    var qa = QUALITY_ORDER[qualityFromName(a.name || a.title || "")] !== void 0 ? QUALITY_ORDER[qualityFromName(a.name || a.title || "")] : 99;
    var qb = QUALITY_ORDER[qualityFromName(b.name || b.title || "")] !== void 0 ? QUALITY_ORDER[qualityFromName(b.name || b.title || "")] : 99;
    return qa - qb;
  });
}
function getStreams(tmdbId, mediaType, season, episode) {
  console.log("[LeFlux. P2P] getStreams", tmdbId, mediaType, season, episode);
  var settings = getSettings();
  var hasAnyKey = settings.c411_apikey || settings.torr9_passkey || settings.tr4ker_apikey || settings.gemini_apikey;
  if (!hasAnyKey) {
    console.log("[Frenchio P2P] No tracker keys configured");
    return Promise.resolve([]);
  }
  return resolveMediaInfoFromTmdb(tmdbId, mediaType).then(function(mediaInfo) {
    if (!mediaInfo.title) {
      console.log("[Frenchio P2P] Could not resolve title from TMDB");
      return [];
    }
    var searches = [
      queryC411(mediaInfo, tmdbId, mediaType, season, episode),
      queryTorr9(mediaInfo, tmdbId, mediaType, season, episode),
      queryGemini(mediaInfo, tmdbId, mediaType, season, episode)
    ];
    if (settings.tr4ker_apikey) {
      searches.push(searchTR4KER(mediaInfo.title, mediaType, season, episode));
    }
    return Promise.all(searches);
  }).then(function(results) {
    var all = [];
    results.forEach(function(list) {
      all = all.concat(list);
    });
    var unique = dedup(all);
    var sorted = sortStreams(unique);
    var top15 = sorted.slice(0, 15);
    console.log("[Frenchio P2P] Found", sorted.length, "streams. Formatting top", top15.length);
    var formatted = top15.map(function(t) {
      var trackerName = "LeFlux.";
      if (t.source === "c411")
        trackerName = "C411";
      else if (t.source === "torr9")
        trackerName = "Torr9";
      else if (t.source === "gemini")
        trackerName = "Gemini";
      else if (t.source === "tr4ker")
        trackerName = "TR4KER";
      var size = t.size || 0;
      if (mediaType === "tv" && season !== null && season !== void 0 && episode !== null && episode !== void 0) {
        size = estimateEpisodeSize(t.name || t.title || "", size);
      }
      var torrentCopy = {};
      for (var k in t) {
        torrentCopy[k] = t[k];
      }
      torrentCopy.size = size;
      var streamUrl = getStreamUrl(torrentCopy, settings);
      return formatStream(torrentCopy, trackerName, streamUrl);
    }).filter(Boolean);
    return formatted;
  }).catch(function(e) {
    console.log("[Frenchio P2P] Fatal error:", e.message);
    return [];
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { getStreams };
}
if (typeof globalThis !== "undefined") {
  globalThis.getStreams = getStreams;
} else if (typeof global !== "undefined") {
  global.getStreams = getStreams;
} else if (typeof window !== "undefined") {
  window.getStreams = getStreams;
} else {
  exports.getStreams = getStreams;
}
