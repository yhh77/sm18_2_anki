#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 items_extracted.json 生成 .apkg 文件 (Modern v3 格式, schema v18)

依赖: pip install fsrs zstandard
用法: python items2apkg.py [items_extracted.json] [output.apkg]
"""

import json, sys, os, sqlite3, zipfile, time, random, string, re, urllib.parse, struct, hashlib
from datetime import datetime, timezone
from fsrs import Scheduler, Card, Rating, ReviewLog

UTC = timezone.utc


# ═══════════════════════════════════════════════════════════
# FSRS Simulation
# ═══════════════════════════════════════════════════════════

def next_interval(S, r=0.9):
    """FSRS: predict interval from stability S at desired retention r.
    When r matches the decay parameter, interval ≈ stability."""
    return max(1, round(S))


def sm_to_rating(g):
    if g in (1, 2): return Rating.Again
    if g == 3:      return Rating.Hard
    return Rating.Good


def fsrs_simulate(rephist):
    s = Scheduler(enable_fuzzing=False)
    card = Card(card_id=1)
    logs = []

    for i, r in enumerate(rephist):
        g = r.get('Grade', 0)
        if g > 5:
            if i == 0: rating = Rating.Good
            else:      continue
        else:
            rating = sm_to_rating(g)

        d_str = r.get('Date', '')
        h = r.get('Hour', 0)
        try:
            hi = int(h)
            mi = int((h - hi) * 60 + 0.5)
            dt = datetime.strptime(f'{d_str} {hi:02d}:{mi:02d}',
                                   '%d.%m.%Y %H:%M').replace(tzinfo=UTC)
        except (ValueError, OverflowError):
            dt = datetime.strptime(d_str, '%d.%m.%Y').replace(tzinfo=UTC)

        logs.append(ReviewLog(card_id=1, rating=rating,
                              review_datetime=dt, review_duration=None))

    if len(logs) < 1:
        return 1.0, 1.0, logs
    if len(logs) == 1:
        card, _ = s.review_card(card, logs[0].rating, logs[0].review_datetime)
        return (card.stability or 1.0), (card.difficulty or 1.0), logs

    updated = s.reschedule_card(card, logs)
    return (updated.stability or 1.0), (updated.difficulty or 1.0), logs


# ═══════════════════════════════════════════════════════════
# Protobuf Encoding (manual, no proto compiler needed)
# ═══════════════════════════════════════════════════════════

def _enc_varint(value):
    """Encode uint64 value as protobuf varint bytes."""
    parts = []
    while value > 0x7f:
        parts.append((value & 0x7f) | 0x80)
        value >>= 7
    parts.append(value)
    return bytes(parts) if parts else b'\x00'


def _pb_tag_wire(field_num, wire_type, payload):
    """Encode a protobuf field: tag + payload."""
    tag = _enc_varint((field_num << 3) | wire_type)
    return tag + payload


def _pb_varint(field_num, value):
    return _pb_tag_wire(field_num, 0, _enc_varint(value))


def _pb_uint32(field_num, value):
    return _pb_varint(field_num, value)


def _pb_int64(field_num, value):
    return _pb_varint(field_num, value)


def _pb_sint32(field_num, value):
    zigzag = (value << 1) ^ (value >> 31)
    return _pb_varint(field_num, zigzag)


def _pb_bool(field_num, value):
    return _pb_varint(field_num, 1 if value else 0)


def _pb_enum(field_num, value):
    return _pb_varint(field_num, value)


def _pb_float(field_num, value):
    return _pb_tag_wire(field_num, 5, struct.pack('<f', value))


def _pb_bytes(field_num, data):
    return _pb_tag_wire(field_num, 2, _enc_varint(len(data)) + data)


def _pb_string(field_num, s):
    return _pb_bytes(field_num, s.encode('utf-8'))


def _pb_packed_float(field_num, values):
    packed = b''.join(struct.pack('<f', v) for v in values)
    return _pb_bytes(field_num, packed)


def _pb_packed_uint32(field_num, values):
    packed = b''.join(_enc_varint(v) for v in values)
    return _pb_bytes(field_num, packed)


# ═══════════════════════════════════════════════════════════
# Protobuf Message Builders
# ═══════════════════════════════════════════════════════════

def build_field_config(font_name='Arial', font_size=20):
    """Notetype.Field.Config"""
    parts = []
    parts.append(_pb_bool(1, False))         # sticky
    parts.append(_pb_bool(2, False))         # rtl
    parts.append(_pb_string(3, font_name))
    parts.append(_pb_uint32(4, font_size))
    parts.append(_pb_string(5, ''))          # description
    parts.append(_pb_bool(6, False))         # plain_text
    parts.append(_pb_bool(7, False))         # collapsed
    parts.append(_pb_bool(8, False))         # exclude_from_search
    parts.append(_pb_bool(11, False))        # prevent_deletion
    return b''.join(parts)


def build_template_config(q_format='{{Front}}',
                           a_format='{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}'):
    """Notetype.Template.Config"""
    parts = []
    parts.append(_pb_string(1, q_format))
    parts.append(_pb_string(2, a_format))
    parts.append(_pb_string(3, ''))          # q_format_browser
    parts.append(_pb_string(4, ''))          # a_format_browser
    parts.append(_pb_int64(5, 0))            # target_deck_id
    parts.append(_pb_string(6, ''))          # browser_font_name
    parts.append(_pb_uint32(7, 0))           # browser_font_size
    return b''.join(parts)


def build_notetype_config(css, kind=0, sort_field_idx=0, reqs=None):
    """Notetype.Config"""
    parts = []
    parts.append(_pb_enum(1, kind))          # 0=NORMAL, 1=CLOZE
    parts.append(_pb_uint32(2, sort_field_idx))
    parts.append(_pb_string(3, css))
    if reqs:
        for req_bytes in reqs:
            parts.append(_pb_bytes(8, req_bytes))
    return b''.join(parts)


def build_card_requirement(card_ord, kind=2, field_ords=None):
    """Notetype.Config.CardRequirement: kind=2=ALL"""
    if field_ords is None:
        field_ords = [0, 1]
    parts = []
    parts.append(_pb_uint32(1, card_ord))
    parts.append(_pb_enum(2, kind))
    parts.append(_pb_packed_uint32(3, field_ords))
    return b''.join(parts)


def build_deck_common():
    """Deck.Common"""
    parts = []
    parts.append(_pb_bool(1, False))         # study_collapsed
    parts.append(_pb_bool(2, False))         # browser_collapsed
    parts.append(_pb_uint32(3, 0))           # last_day_studied
    parts.append(_pb_uint32(4, 0))           # new_studied
    parts.append(_pb_uint32(5, 0))           # review_studied
    parts.append(_pb_uint32(6, 0))           # learning_studied
    parts.append(_pb_uint32(7, 0))           # milliseconds_studied
    return b''.join(parts)


def build_deck_kind_normal(config_id=1):
    """Deck.KindContainer with normal kind (field 1)."""
    inner = b''
    inner += _pb_uint32(1, config_id)
    inner += _pb_uint32(2, 0)                # extend_new
    inner += _pb_uint32(3, 0)                # extend_review
    inner += _pb_string(4, '')               # description
    return _pb_bytes(1, inner)


def build_deck(did, name, common, kind, mtime_secs=0, usn=-1):
    """Deck top-level message."""
    parts = []
    parts.append(_pb_int64(1, did))
    parts.append(_pb_string(2, name))
    parts.append(_pb_uint32(3, mtime_secs))
    parts.append(_pb_sint32(4, usn))
    parts.append(_pb_bytes(5, common))
    parts.append(kind)
    return b''.join(parts)


def build_deck_config_config(learn_steps=None, relearn_steps=None,
                              fsrs_params_5=None, desired_retention=0.9):
    """DeckConfig.Config"""
    if learn_steps is None:
        learn_steps = [1.0, 10.0]
    if relearn_steps is None:
        relearn_steps = [10.0]
    if fsrs_params_5 is None:
        fsrs_params_5 = [0.4026, 0.8248, 1.0337, 16.0139, 4.9129]

    parts = []
    parts.append(_pb_packed_float(1, learn_steps))
    parts.append(_pb_packed_float(2, relearn_steps))
    parts.append(_pb_packed_float(5, fsrs_params_5))
    parts.append(_pb_packed_float(4, [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]))
    parts.append(_pb_uint32(9, 20))          # new_per_day
    parts.append(_pb_uint32(10, 200))        # reviews_per_day
    parts.append(_pb_float(11, 2.5))         # initial_ease
    parts.append(_pb_float(12, 1.3))         # easy_multiplier
    parts.append(_pb_float(13, 1.2))         # hard_multiplier
    parts.append(_pb_float(14, 0.0))         # lapse_multiplier
    parts.append(_pb_float(15, 1.0))         # interval_multiplier
    parts.append(_pb_uint32(16, 36500))      # maximum_review_interval
    parts.append(_pb_uint32(17, 1))          # minimum_lapse_interval
    parts.append(_pb_uint32(18, 1))          # graduating_interval_good
    parts.append(_pb_uint32(19, 4))          # graduating_interval_easy
    parts.append(_pb_enum(20, 0))            # new_card_insert_order=DUE
    parts.append(_pb_enum(21, 1))            # leech_action=TAG_ONLY
    parts.append(_pb_uint32(22, 8))          # leech_threshold
    parts.append(_pb_bool(23, False))        # disable_autoplay
    parts.append(_pb_uint32(24, 0))          # cap_answer_time_to_secs
    parts.append(_pb_bool(25, False))        # show_timer
    parts.append(_pb_bool(26, True))         # skip_question_when_replaying_answer
    parts.append(_pb_bool(27, False))        # bury_new
    parts.append(_pb_bool(28, False))        # bury_reviews
    parts.append(_pb_bool(29, False))        # bury_interday_learning
    parts.append(_pb_enum(30, 0))            # new_mix=MIX_WITH_REVIEWS
    parts.append(_pb_enum(31, 0))            # interday_learning_mix
    parts.append(_pb_enum(32, 0))            # new_card_sort_order=TEMPLATE
    parts.append(_pb_enum(33, 0))            # review_order=DAY
    parts.append(_pb_enum(34, 0))            # new_card_gather_priority=DECK
    parts.append(_pb_uint32(35, 0))          # new_per_day_minimum
    parts.append(_pb_enum(36, 0))            # question_action=SHOW_ANSWER
    parts.append(_pb_float(37, desired_retention))
    parts.append(_pb_bool(38, False))        # stop_timer_on_answer
    parts.append(_pb_float(41, 0.0))         # seconds_to_show_question
    parts.append(_pb_float(42, 0.0))         # seconds_to_show_answer
    parts.append(_pb_enum(43, 0))            # answer_action=BURY_CARD
    parts.append(_pb_bool(44, True))         # wait_for_audio
    return b''.join(parts)


def build_deck_config_msg(dcid, name, config_bytes, mtime_secs=0, usn=0):
    """DeckConfig top-level message."""
    parts = []
    parts.append(_pb_int64(1, dcid))
    parts.append(_pb_string(2, name))
    parts.append(_pb_uint32(3, mtime_secs))
    parts.append(_pb_sint32(4, usn))
    parts.append(_pb_bytes(5, config_bytes))
    return b''.join(parts)


def build_media_entries(media_files):
    """MediaEntries protobuf message."""
    entries = b''
    for filename, data in media_files:
        entry = b''
        entry += _pb_string(1, filename)
        entry += _pb_uint32(2, len(data))
        entry += _pb_bytes(3, hashlib.sha1(data).digest())
        entries += _pb_bytes(1, entry)
    return entries


# ═══════════════════════════════════════════════════════════
# Image Handling
# ═══════════════════════════════════════════════════════════

def read_content(source):
    if source is None: return None
    s = source.strip()
    if not s: return None
    if s[1:3] == ':\\' or s.startswith('/') or ('elements' in s and '.HTM' in s.upper()):
        try:
            with open(s, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception:
            return '(error: {})'.format(os.path.basename(s))
    return s


class ImageCollector:
    def __init__(self):
        self.media_map = {}
        self.media_files = {}
        self.counter = 0

    def process_html(self, html_param, base_dir=''):
        def replace_img(m):
            tag = m.group(0)
            src_m = re.search(r'src\s*=\s*"([^"]+)"', tag)
            if not src_m: return tag
            src = src_m.group(1)

            if '127.0.0.1:50555/formula' in src:
                qm = src.find('latex=')
                if qm > 0:
                    latex = urllib.parse.unquote(src[qm + 6:])
                    return r'\({}\)'.format(latex)
                return ''

            if src.startswith('file:///'):
                local = src[8:].replace('/', '\\')
                local = urllib.parse.unquote(local).split('?')[0]
                name = self._add_file(local)
                if name: return tag.replace(src, name)

            if src.startswith('http://') or src.startswith('https://'):
                name = self._download(src)
                if name: return tag.replace(src, name)

            return tag

        return re.sub(r'<img[^>]+>', replace_img, html_param, flags=re.IGNORECASE)

    def _add_file(self, path):
        path = path.strip()
        if not os.path.exists(path): return None
        if path in self.media_map: return self.media_map[path]
        real_name = os.path.basename(path)
        number = str(self.counter); self.counter += 1
        try:
            with open(path, 'rb') as f:
                self.media_files[number] = (f.read(), real_name)
            self.media_map[path] = real_name
            return real_name
        except Exception: return None

    def _download(self, url):
        if url in self.media_map: return self.media_map[url]
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={'User-Agent': 'SM2Anki'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                ct = resp.headers.get('Content-Type', '')
            ext = '.jpg'
            if 'png' in ct: ext = '.png'
            elif 'gif' in ct: ext = '.gif'
            elif 'svg' in ct: ext = '.svg'
            elif 'webp' in ct: ext = '.webp'
            real_name = str(self.counter) + ext
            number = str(self.counter); self.counter += 1
            self.media_files[number] = (data, real_name)
            self.media_map[url] = real_name
            return real_name
        except Exception: return None

    def get_media_entries(self):
        """Returns list of (number_str, real_name, data) for ZIP packaging."""
        return [(num, real_name, data)
                for num, (data, real_name) in self.media_files.items()]


def build_card_html(front_srcs, back_srcs, img_collector):
    front_parts, back_parts = [], []
    for src in front_srcs:
        c = read_content(src)
        if c:
            c = img_collector.process_html(c)
            if c.strip(): front_parts.append(c)
    for src in back_srcs:
        c = read_content(src)
        if c:
            c = img_collector.process_html(c)
            if c.strip(): back_parts.append(c)
    return '<hr>'.join(front_parts) if front_parts else '', \
           '<hr>'.join(back_parts) if back_parts else ''


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def create_schema(conn):
    """Create Anki schema v18 tables."""
    # Register Anki's unicase collation (case-insensitive)
    def _unicase(s1, s2):
        a = s1.lower(); b = s2.lower()
        if a < b: return -1
        if a > b: return 1
        return 0
    conn.create_collation('unicase', _unicase)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE col (
      id integer PRIMARY KEY,
      crt integer NOT NULL,
      mod integer NOT NULL,
      scm integer NOT NULL,
      ver integer NOT NULL,
      dty integer NOT NULL,
      usn integer NOT NULL,
      ls integer NOT NULL,
      conf text NOT NULL,
      models text NOT NULL,
      decks text NOT NULL,
      dconf text NOT NULL,
      tags text NOT NULL
    );
    CREATE TABLE notes (
      id integer PRIMARY KEY,
      guid text NOT NULL,
      mid integer NOT NULL,
      mod integer NOT NULL,
      usn integer NOT NULL,
      tags text NOT NULL,
      flds text NOT NULL,
      sfld integer NOT NULL,
      csum integer NOT NULL,
      flags integer NOT NULL,
      data text NOT NULL
    );
    CREATE TABLE cards (
      id integer PRIMARY KEY,
      nid integer NOT NULL,
      did integer NOT NULL,
      ord integer NOT NULL,
      mod integer NOT NULL,
      usn integer NOT NULL,
      type integer NOT NULL,
      queue integer NOT NULL,
      due integer NOT NULL,
      ivl integer NOT NULL,
      factor integer NOT NULL,
      reps integer NOT NULL,
      lapses integer NOT NULL,
      left integer NOT NULL,
      odue integer NOT NULL,
      odid integer NOT NULL,
      flags integer NOT NULL,
      data text NOT NULL
    );
    CREATE TABLE revlog (
      id integer PRIMARY KEY,
      cid integer NOT NULL,
      usn integer NOT NULL,
      ease integer NOT NULL,
      ivl integer NOT NULL,
      lastIvl integer NOT NULL,
      factor integer NOT NULL,
      time integer NOT NULL,
      type integer NOT NULL
    );
    CREATE TABLE graves (
      oid integer NOT NULL,
      type integer NOT NULL,
      usn integer NOT NULL,
      PRIMARY KEY (oid, type)
    ) WITHOUT ROWID;
    CREATE TABLE notetypes (
      id integer NOT NULL PRIMARY KEY,
      name text NOT NULL COLLATE unicase,
      mtime_secs integer NOT NULL,
      usn integer NOT NULL,
      config blob NOT NULL
    );
    CREATE TABLE fields (
      ntid integer NOT NULL,
      ord integer NOT NULL,
      name text NOT NULL COLLATE unicase,
      config blob NOT NULL,
      PRIMARY KEY (ntid, ord)
    ) WITHOUT ROWID;
    CREATE TABLE templates (
      ntid integer NOT NULL,
      ord integer NOT NULL,
      name text NOT NULL COLLATE unicase,
      mtime_secs integer NOT NULL,
      usn integer NOT NULL,
      config blob NOT NULL,
      PRIMARY KEY (ntid, ord)
    ) WITHOUT ROWID;
    CREATE TABLE decks (
      id integer PRIMARY KEY NOT NULL,
      name text NOT NULL COLLATE unicase,
      mtime_secs integer NOT NULL,
      usn integer NOT NULL,
      common blob NOT NULL,
      kind blob NOT NULL
    );
    CREATE TABLE deck_config (
      id integer PRIMARY KEY NOT NULL,
      name text NOT NULL COLLATE unicase,
      mtime_secs integer NOT NULL,
      usn integer NOT NULL,
      config blob NOT NULL
    );
    CREATE TABLE config (
      key text NOT NULL PRIMARY KEY COLLATE unicase,
      usn integer NOT NULL,
      mtime_secs integer NOT NULL,
      val blob NOT NULL
    ) WITHOUT ROWID;
    CREATE TABLE tags (
      tag text NOT NULL PRIMARY KEY COLLATE unicase,
      usn integer NOT NULL,
      collapsed boolean NOT NULL,
      config blob NULL
    ) WITHOUT ROWID;
    """)


def write_config_table(cur, now_sec, notetype_id, deck_id):
    """Write minimal config entries (plain text values, not protobuf)."""
    entries = [
        ('activeDecks', '[1]'),
        ('addToCur', 'true'),
        ('collapseTime', '1200'),
        ('creationOffset', '-480'),
        ('curDeck', str(deck_id)),
        ('curModel', str(notetype_id)),
        ('dayLearnFirst', 'false'),
        ('dueCounts', 'true'),
        ('estTimes', 'true'),
        ('newSpread', '0'),
        ('nextPos', '2'),
        ('sched2021', 'true'),
        ('schedVer', '2'),
        ('sortBackwards', 'false'),
        ('sortType', 'noteFld'),
        ('timeLim', '0'),
    ]
    for key, val_str in entries:
        cur.execute(
            'INSERT INTO config VALUES (?, -1, ?, ?)',
            (key, now_sec, val_str.encode('utf-8'))
        )


def build_apkg(items, apkg_path='sm_import.apkg'):
    """从 items 列表直接生成 .apkg（纯内存，无中间文件）"""

    now_sec = int(time.time())
    notetype_id = int(time.time() * 1000)
    deck_id = 1
    dconf_id = 1
    USEC = '\x1f'

    # ── Database path ──────────────────────────────────
    db_path = apkg_path.replace('.apkg', '_tmp.anki21')
    if os.path.exists(db_path): os.remove(db_path)
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    cur = conn.cursor()

    # ── Config tables (protobuf blobs) ─────────────────
    css = '.card { font-family: arial; font-size: 20px; text-align: center; }'

    # Notetype.Config only (NOT the full Notetype message)
    notetype_config_blob = build_notetype_config(css, reqs=[build_card_requirement(0)])

    # Field and template configs
    field_cfg_front = build_field_config()
    field_cfg_back = build_field_config()
    tmpl_cfg = build_template_config()

    # Deck
    common_blob = build_deck_common()
    kind_blob = build_deck_kind_normal(config_id=dconf_id)
    deck_blob = build_deck(deck_id, 'SM Import', common_blob, kind_blob)

    # Deck config (only Config sub-message, NOT full DeckConfig)
    dconf_config_blob = build_deck_config_config()

    cur.execute('INSERT INTO notetypes VALUES (?,?,0,-1,?)',
                (notetype_id, 'Basic', notetype_config_blob))
    cur.execute('INSERT INTO fields VALUES (?,0,?,?)',
                (notetype_id, 'Front', field_cfg_front))
    cur.execute('INSERT INTO fields VALUES (?,1,?,?)',
                (notetype_id, 'Back', field_cfg_back))
    cur.execute('INSERT INTO templates VALUES (?,0,?,0,-1,?)',
                (notetype_id, 'Card 1', tmpl_cfg))
    cur.execute('INSERT INTO decks VALUES (?,?,0,-1,?,?)',
                (deck_id, 'SM Import', common_blob, kind_blob))
    cur.execute('INSERT INTO deck_config VALUES (?,?,0,0,?)',
                (dconf_id, 'Default', dconf_config_blob))
    write_config_table(cur, now_sec, notetype_id, deck_id)

    # ── col table (empty conf/models/decks/dconf, data in separate tables) ──
    # Use a fixed early crt so all cards appear overdue → due in Anki's queue
    crt_dt = datetime(2015, 1, 1, tzinfo=UTC)
    crt_ts = int(crt_dt.timestamp())
    crt_ms = crt_ts * 1000
    now_ms = now_sec * 1000
    cur.execute(
        "INSERT INTO col VALUES (1,?,?,?,18,0,0,0,'','','','','')",
        (crt_ts, crt_ms, crt_ts)
    )

    # ── Notes, Cards, Revlog ───────────────────────────
    note_inserts, card_inserts, revlog_inserts = [], [], []
    img_collector = ImageCollector()
    skipped = 0

    for ci, it in enumerate(items):
        el_no = it['ElNo']
        rephist = it['RepHist']

        front_html, back_html = build_card_html(it['Front'], it['Back'], img_collector)
        if not front_html and not back_html: skipped += 1; continue

        final_S, final_D, logs = fsrs_simulate(rephist)
        final_ivl = next_interval(final_S)
        factor = int(max(1300, min(3000, 3000 - (final_D - 1) * 188.9)))

        raw_grades = [r.get('Grade', 0) for i, r in enumerate(rephist)
                      if r.get('Grade', 0) <= 5 or i == 0]
        reps_total = len(raw_grades)
        lapses_total = sum(1 for g in raw_grades if g in (1, 2))


        # Card ID
        first_date_str = rephist[0].get('Date', '01.01.2020') if rephist else '01.01.2020'
        try:
            first_dt = datetime.strptime(first_date_str, '%d.%m.%Y')
            cid = int(first_dt.timestamp() * 1000) + el_no
        except ValueError:
            cid = int(time.time() * 1000) + el_no
        nid = cid

        guid = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
        flds = front_html + USEC + back_html
        sfld_text = front_html[:200] if front_html else back_html[:200]
        note_inserts.append((nid, guid, notetype_id, now_sec, -1, '',
                             flds, sfld_text, 0, 0, ''))

        # Card type/queue
        if len(rephist) == 0:
            card_type = 0; queue_val = 0
            due_val = (el_no % 100000)
            final_ivl = 0; factor = 0
        else:
            card_type = 2; queue_val = 2
            # due = (last_review_date - crt) + ivl, in days
            if len(logs) > 0:
                last_dt = sorted(logs, key=lambda l: l.review_datetime)[-1].review_datetime
                days_from_crt_to_last = (last_dt - crt_dt).days
                due_val = days_from_crt_to_last + final_ivl
            else:
                due_val = max(1, final_ivl)

        card_data = json.dumps({
            'pos': ci,
            's': round(final_S, 4),       # 官方: round_to_places(v, 4)
            'd': round(final_D, 4),
            'dr': 0.9
        }, ensure_ascii=False)

        card_inserts.append((cid, nid, deck_id, 0, now_sec, -1,
                             card_type, queue_val, due_val,
                             final_ivl, factor, reps_total, lapses_total,
                             0, 0, 0, 0, card_data))

        # Revlog entries
        if len(logs) < 1: continue
        prev_ivl = 0
        sorted_logs = sorted(logs, key=lambda l: l.review_datetime)

        for j, log in enumerate(sorted_logs):
            anki_ease = log.rating.value

            # Determine revlog type
            orig_g = raw_grades[min(j, len(raw_grades) - 1)] if raw_grades else 0
            if j == 0:
                rtype = 0  # learn
            elif orig_g in (1, 2):
                rtype = 2  # relearn
            else:
                rtype = 1  # review

            # Ivl value based on type
            if rtype == 0:
                if anki_ease == 1:
                    ivl_val = -900
                elif anki_ease == 4:
                    ivl_val = 18  # Easy: approximate graduation interval
                else:
                    ivl_val = 1  # Good/Hard
            elif rtype == 2:
                ivl_val = -600
            else:
                if j + 1 < len(sorted_logs):
                    delta = (sorted_logs[j+1].review_datetime - log.review_datetime).days
                    ivl_val = max(1, delta)
                else:
                    ivl_val = max(1, round(final_S))

            lastIvl_val = prev_ivl
            if ivl_val > 0:
                prev_ivl = ivl_val

            # Factor (FSRS: 100-1100, SM-2: 1300-3000; cards.factor uses SM-2, revlog.factor uses FSRS)
            if rtype == 0:
                f_val = {1: 250, 2: 250, 3: 250, 4: 347}.get(anki_ease, 250)
            else:
                # FSRS-normalized: difficulty * 100, clamped to 100-1100
                f_val = int(max(100, min(1100, final_D * 100)))

            rid = int(log.review_datetime.timestamp() * 1000) + (ci % 1000)
            revlog_inserts.append((rid, cid, -1, anki_ease, ivl_val, lastIvl_val,
                                   f_val, 0, rtype))

    cur.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)", note_inserts)
    cur.executemany("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", card_inserts)
    cur.executemany("INSERT INTO revlog VALUES (?,?,?,?,?,?,?,?,?)", revlog_inserts)
    conn.commit()
    conn.close()

    # ── Stub database (collection.anki2, v11) ──────────
    stub_db_path = db_path.replace('.anki21', '_stub.anki2')
    if os.path.exists(stub_db_path): os.remove(stub_db_path)
    sc = sqlite3.connect(stub_db_path)
    sc.executescript("""
    PRAGMA page_size=512;
    CREATE TABLE col (id integer PRIMARY KEY, crt integer NOT NULL, mod integer NOT NULL,
      scm integer NOT NULL, ver integer NOT NULL, dty integer NOT NULL, usn integer NOT NULL,
      ls integer NOT NULL, conf text NOT NULL, models text NOT NULL, decks text NOT NULL,
      dconf text NOT NULL, tags text NOT NULL);
    CREATE TABLE cards (id integer PRIMARY KEY, nid integer NOT NULL, did integer NOT NULL,
      ord integer NOT NULL, mod integer NOT NULL, usn integer NOT NULL, type integer NOT NULL,
      queue integer NOT NULL, due integer NOT NULL, ivl integer NOT NULL, factor integer NOT NULL,
      reps integer NOT NULL, lapses integer NOT NULL, left integer NOT NULL, odue integer NOT NULL,
      odid integer NOT NULL, flags integer NOT NULL, data text NOT NULL);
    CREATE TABLE notes (id integer PRIMARY KEY, guid text NOT NULL, mid integer NOT NULL,
      mod integer NOT NULL, usn integer NOT NULL, tags text NOT NULL, flds text NOT NULL,
      sfld integer NOT NULL, csum integer NOT NULL, flags integer NOT NULL, data text NOT NULL);
    CREATE TABLE revlog (id integer PRIMARY KEY, cid integer NOT NULL, usn integer NOT NULL,
      ease integer NOT NULL, ivl integer NOT NULL, lastIvl integer NOT NULL,
      factor integer NOT NULL, time integer NOT NULL, type integer NOT NULL);
    CREATE TABLE graves (usn integer NOT NULL, oid integer NOT NULL, type integer NOT NULL);
    """)
    # Stub note: prompts user to update Anki
    stub_note_id = int(time.time() * 1000)
    stub_guid = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    sc.execute("INSERT INTO col VALUES (1,?,?,?,11,0,0,0,'{}','{}','{}','{}','{}')",
               (now_sec, now_ms, now_sec))
    sc.execute("INSERT INTO notes VALUES (?,?,?,?,0,'',"
               "'请更新 Anki 至最新版本，然后再二次导入 .colpkg/.apkg 文件'||char(31)||'',"
               "'请更新 Anki 至最新版本',0,0,'')",
               (stub_note_id, stub_guid, notetype_id, now_sec))
    sc.execute("INSERT INTO cards VALUES (?,?,1,0,?,0,0,0,1,0,0,0,0,0,0,0,0,'{}')",
               (stub_note_id, stub_note_id, now_sec))
    sc.commit()
    sc.execute("PRAGMA journal_mode=delete")
    sc.execute("VACUUM")
    sc.close()

    # ── Package APKG ──────────────────────────────────
    try:
        import zstandard as zstd
        params = zstd.ZstdCompressionParameters(
            compression_level=0,
            write_content_size=False,
            write_checksum=False,
            write_dict_id=False,
        )
        cctx = zstd.ZstdCompressor(compression_params=params)
        with open(db_path, 'rb') as f:
            compressed_db = cctx.compress(f.read())
    except ImportError:
        print('WARNING: zstandard not installed, using uncompressed database')
        with open(db_path, 'rb') as f:
            compressed_db = f.read()

    # Media protobuf + zstd
    media_entries = img_collector.get_media_entries()
    # MediaEntry protobuf: name=real filename, size, sha1
    media_proto = build_media_entries([(real_name, data) for _, real_name, data in media_entries])
    try:
        media_compressed = cctx.compress(media_proto)
    except (ImportError, NameError):
        media_compressed = media_proto

    # ── Legacy v11 database (for Python importer) ──────
    v11_db_path = apkg_path.replace('.apkg', '_v11.anki21')
    if os.path.exists(v11_db_path): os.remove(v11_db_path)
    v11_conn = sqlite3.connect(v11_db_path)
    v11_conn.create_collation('unicase', lambda a, b: 0)
    v11 = v11_conn.cursor()
    v11.executescript("""
    CREATE TABLE col (id integer PRIMARY KEY, crt integer NOT NULL, mod integer NOT NULL,
      scm integer NOT NULL, ver integer NOT NULL, dty integer NOT NULL, usn integer NOT NULL,
      ls integer NOT NULL, conf text NOT NULL, models text NOT NULL, decks text NOT NULL,
      dconf text NOT NULL, tags text NOT NULL);
    CREATE TABLE cards (id integer PRIMARY KEY, nid integer NOT NULL, did integer NOT NULL,
      ord integer NOT NULL, mod integer NOT NULL, usn integer NOT NULL, type integer NOT NULL,
      queue integer NOT NULL, due integer NOT NULL, ivl integer NOT NULL, factor integer NOT NULL,
      reps integer NOT NULL, lapses integer NOT NULL, left integer NOT NULL, odue integer NOT NULL,
      odid integer NOT NULL, flags integer NOT NULL, data text NOT NULL);
    CREATE TABLE notes (id integer PRIMARY KEY, guid text NOT NULL, mid integer NOT NULL,
      mod integer NOT NULL, usn integer NOT NULL, tags text NOT NULL, flds text NOT NULL,
      sfld text NOT NULL, csum integer NOT NULL, flags integer NOT NULL, data text NOT NULL);
    CREATE TABLE revlog (id integer PRIMARY KEY, cid integer NOT NULL, usn integer NOT NULL,
      ease integer NOT NULL, ivl integer NOT NULL, lastIvl integer NOT NULL,
      factor integer NOT NULL, time integer NOT NULL, type integer NOT NULL);
    CREATE TABLE graves (usn integer NOT NULL, oid integer NOT NULL, type integer NOT NULL);
    """)
    # Build JSON config for v11
    conf = {"activeDecks": [1], "curDeck": 1, "curModel": notetype_id,
            "schedVer": 2, "creationOffset": -480, "sched2021": True,
            "estTimes": True, "nextPos": 2, "collapseTime": 1200, "dueCounts": True,
            "addToCur": True, "sortType": "noteFld", "sortBackwards": False,
            "newSpread": 0, "timeLim": 0, "dayLearnFirst": False}
    v11_conf = json.dumps(conf)
    v11_models = json.dumps({str(notetype_id): {
        "id": notetype_id, "name": "Basic", "type": 0, "mod": now_sec, "usn": -1,
        "sortf": 0, "did": None,
        "flds": [{"name": "Front", "ord": 0, "sticky": False, "rtl": False, "font": "Arial",
                  "size": 20, "description": "", "plainText": False, "collapsed": False,
                  "excludeFromSearch": False, "id": 2000000000001, "tag": None, "preventDeletion": False},
                 {"name": "Back", "ord": 1, "sticky": False, "rtl": False, "font": "Arial",
                  "size": 20, "description": "", "plainText": False, "collapsed": False,
                  "excludeFromSearch": False, "id": 2000000000002, "tag": None, "preventDeletion": False}],
        "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}",
                   "afmt": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}",
                   "bqfmt": "", "bafmt": "", "did": None, "bfont": "", "bsize": 0,
                   "id": 3000000000001}],
        "css": css
    }})
    v11_decks = json.dumps({"1": {"id": 1, "mod": 0, "name": "SM Import", "usn": -1,
        "mid": notetype_id, "lrnToday": [0, 0], "revToday": [0, 0], "newToday": [0, 0],
        "timeToday": [0, 0], "collapsed": False, "browserCollapsed": False, "desc": "",
        "dyn": 0, "conf": 1, "extendNew": 0, "extendRev": 0}})
    v11_dconf = json.dumps({"1": {"id": 1, "name": "Default", "mod": 0, "usn": -1,
        "maxTaken": 60, "autoplay": True, "timer": 0, "replayq": True,
        "new": {"bury": False, "delays": [1.0, 10.0], "initialFactor": 2500,
                "ints": [1, 4, 0], "order": 1, "perDay": 20},
        "rev": {"bury": False, "ease4": 1.3, "ivlFct": 1.0, "maxIvl": 36500,
                "perDay": 200, "hardFactor": 1.2},
        "lapse": {"delays": [10.0], "leechAction": 1, "leechFails": 8, "minInt": 1, "mult": 0.0},
        "dyn": False, "newMix": 0, "newPerDayMinimum": 0, "interdayLearningMix": 0,
        "reviewOrder": 0, "newSortOrder": 0, "newGatherPriority": 0, "buryInterdayLearning": False,
        "desiredRetention": 0.9, "fsrsWeights": [], "fsrsParams5": [],
        "ignoreRevlogsBeforeDate": "", "easyDaysPercentages": [1.0]*7,
        "stopTimerOnAnswer": False, "secondsToShowQuestion": 0.0, "secondsToShowAnswer": 0.0,
        "questionAction": 0, "answerAction": 0, "waitForAudio": True, "sm2Retention": 0.9,
        "weightSearch": ""}})
    v11.execute("INSERT INTO col VALUES (1,?,?,?,11,0,0,0,?,?,?,?,?)",
                (crt_ts, now_ms, now_sec, v11_conf, v11_models, v11_decks, v11_dconf, "{}"))
    v11.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)", note_inserts)
    v11.executemany("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", card_inserts)
    v11.executemany("INSERT INTO revlog VALUES (?,?,?,?,?,?,?,?,?)", revlog_inserts)
    v11_conn.commit()
    v11_conn.close()

    # ── Package APKG ──────────────────────────────────
    # Use Legacy 2 format for maximum compatibility (works with both Python & Rust importers)
    # Python importer (AnkiConnect/GUI): reads collection.anki21 + JSON media
    # Rust importer: reads meta -> VERSION_LEGACY_2 -> collection.anki21 + JSON media
    media_json = json.dumps({num: real_name for num, real_name, _ in media_entries})

    with zipfile.ZipFile(apkg_path, 'w', zipfile.ZIP_STORED) as zf:
        # Main database: v11 schema (compatible with both importers)
        zf.writestr('collection.anki21', open(v11_db_path, 'rb').read())
        # Stub for old Anki
        zf.writestr('collection.anki2', open(stub_db_path, 'rb').read())
        # JSON media map
        zf.writestr('media', media_json.encode('utf-8'))
        # Legacy 2 meta
        zf.writestr('meta', b'\x08\x02')  # VERSION_LEGACY_2
        # Media files: raw (not zstd compressed, Python importer needs them raw)
        for number, _, data in media_entries:
            zf.writestr(number, data)  # Raw bytes, no compression

    os.remove(db_path); os.remove(stub_db_path); os.remove(v11_db_path)

    print('Output: {} ({:.0f} KB)'.format(apkg_path, os.path.getsize(apkg_path)/1024))
    print('  Cards: {}  |  Notes: {}  |  Revlog: {}  |  Media: {}  |  Skipped: {}'.format(
        len(card_inserts), len(note_inserts), len(revlog_inserts),
        len(media_entries), skipped))


def main():
    if len(sys.argv) >= 2: input_path = sys.argv[1]
    else: input_path = 'items_extracted.json'
    if len(sys.argv) >= 3: apkg_path = sys.argv[2]
    else: apkg_path = 'sm_import.apkg'

    with open(input_path, 'r', encoding='utf-8') as f:
        items = json.load(f)
    print('Input: {} ({} items)'.format(input_path, len(items)))
    build_apkg(items, apkg_path)


if __name__ == '__main__':
    main()
