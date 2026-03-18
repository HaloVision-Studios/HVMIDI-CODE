import os
import sys
import math
import time
import numpy as np
import itertools
from collections import Counter
from datetime import datetime
import mido

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ==========================================
# CONSTANTS & CONFIGURATION
# ==========================================

SCALES = {
    'major': [0, 2, 4, 5, 7, 9, 11],
    'minor': [0, 2, 3, 5, 7, 8, 10],  # aeolian
    'phrygian_dominant': [0, 1, 4, 5, 7, 8, 10],
    'minor_pentatonic': [0, 3, 5, 7, 10]
}

CHORD_TEMPLATES = {
    'maj': [0, 4, 7], 'min': [0, 3, 7], 'dim': [0, 3, 6],
    'aug': [0, 4, 8], 'dom7': [0, 4, 7, 10], 'maj7': [0, 4, 7, 11], 'min7': [0, 3, 7, 10]
}

KK_PROFILES = {
    'major': np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
    'minor': np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
}

VELOCITY_GRID = [115, 60, 90, 65, 105, 55, 85, 60, 110, 65, 95, 70, 100, 60, 80, 50]

MIDI_CC = {
    'mod_wheel': 1, 'breath': 2, 'volume': 7, 'pan': 10,
    'expression': 11, 'sustain': 64, 'resonance': 71, 'brightness': 74
}

GENRE_PROFILES = {
    "Lo-Fi": {"scale_strictness": 0.7, "allow_7ths_9ths": True, "swing_percent": 62.0, "quantize_strictness": 0.5,
              "max_dissonance_threshold": 8.0, "enforce_parallel_5ths": True, "sustain_automation": False},
    "EDM": {"scale_strictness": 1.0, "allow_7ths_9ths": False, "swing_percent": 50.0, "quantize_strictness": 1.0,
            "max_dissonance_threshold": 2.0, "enforce_parallel_5ths": True, "sustain_automation": False},
    "Metal": {"scale_strictness": 0.9, "allow_7ths_9ths": False, "swing_percent": 50.0, "quantize_strictness": 0.95,
              "max_dissonance_threshold": 12.0, "enforce_parallel_5ths": False, "sustain_automation": False},
    "Jazz": {"scale_strictness": 0.6, "allow_7ths_9ths": True, "swing_percent": 66.0, "quantize_strictness": 0.7,
             "max_dissonance_threshold": 9.0, "enforce_parallel_5ths": True, "sustain_automation": True},
    "Classical": {"scale_strictness": 1.0, "allow_7ths_9ths": False, "swing_percent": 50.0, "quantize_strictness": 0.9,
                  "max_dissonance_threshold": 3.0, "enforce_parallel_5ths": True, "sustain_automation": False},
    "Ambient": {"scale_strictness": 0.2, "allow_7ths_9ths": True, "swing_percent": 50.0, "quantize_strictness": 0.0,
                "max_dissonance_threshold": 15.0, "enforce_parallel_5ths": False, "sustain_automation": True}
}

# ==========================================
# LOGGING SYSTEM
# ==========================================
log_file_path = ""


def setup_logging():
    global log_file_path
    log_dir = os.path.expanduser("~/.HVMIDI_Aligner")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(log_dir, f"aligner_log_{timestamp}.txt")
    with open(log_file_path, 'w') as f:
        f.write(f"--- HVMIDI Aligner Log Initialized: {timestamp} ---\n")


def log_step(msg, show_step=True):
    if show_step:
        print(f"[ALIGNER] {msg}")
    with open(log_file_path, 'a') as f:
        f.write(f"{datetime.now().strftime('%H:%M:%S')} - {msg}\n")


# ==========================================
# PART 1: PSYCHOACOUSTICS & SENSORY ROUGHNESS
# ==========================================

def midi_to_freq(midi_note: float) -> float:
    return 440.0 * (2.0 ** ((midi_note - 69.0) / 12.0))


def freq_to_erb_rate(f: float) -> float:
    return 21.4 * np.log10(4.37 * (f / 1000.0) + 1.0)


def critical_bandwidth(f: float) -> float:
    return 24.7 * (4.37 * (f / 1000.0) + 1.0)


def sethares_kernel(f1: float, f2: float, a1: float, a2: float) -> float:
    if f1 > f2:
        f1, f2, a1, a2 = f2, f1, a2, a1
    f_diff = f2 - f1
    a_min = min(a1, a2)
    s = 0.24 / (0.0207 * f1 + 18.96)
    C1, C2, A1, A2 = 5.0, -5.0, -3.51, -5.75
    dissonance = a_min * (C1 * np.exp(A1 * s * f_diff) + C2 * np.exp(A2 * s * f_diff))
    return max(0.0, dissonance)


def calculate_chord_roughness(midi_notes: list) -> float:
    partials = []
    for note in midi_notes:
        f0 = midi_to_freq(note)
        for n in range(1, 7):
            partials.append({'freq': f0 * n, 'amp': 1.0 / n})
    total_roughness = 0.0
    for p1, p2 in itertools.combinations(partials, 2):
        total_roughness += sethares_kernel(p1['freq'], p2['freq'], p1['amp'], p2['amp'])
    return total_roughness


# ==========================================
# PART 2: HARMONIC ALIGNMENT & PITCH
# ==========================================

def snap_to_scale(midi_note: int, root_note: int, scale_type: str) -> int:
    scale_intervals = SCALES.get(scale_type, SCALES['major'])
    allowed_pcs = [(root_note + i) % 12 for i in scale_intervals]
    pc = midi_note % 12
    if pc in allowed_pcs:
        return midi_note
    for offset in range(1, 7):
        if (pc + offset) % 12 in allowed_pcs: return midi_note + offset
        if (pc - offset) % 12 in allowed_pcs: return midi_note - offset
    return midi_note


def enforce_low_interval_limits(chord_notes: list) -> list:
    chord_notes = sorted(chord_notes)
    if len(chord_notes) < 2: return chord_notes
    bottom, next_note = chord_notes[0], chord_notes[1]
    interval = next_note - bottom
    if bottom < 36 and interval < 7:
        chord_notes[1] += 12
    elif bottom < 48 and interval < 3:
        chord_notes[1] += 12
    return sorted(chord_notes)


def infer_chord(notes: list):
    pitch_classes = [n % 12 for n in notes]
    best_score, best_chord = -float('inf'), None
    for root in range(12):
        for name, template in CHORD_TEMPLATES.items():
            chord_pcs = [(root + i) % 12 for i in template]
            score = -1.0 if chord_pcs[1] not in pitch_classes else 0
            for pc in pitch_classes:
                if pc in chord_pcs:
                    score += 1.0
                else:
                    score -= 0.5
            if score > best_score:
                best_score, best_chord = score, (root, name)
    return best_chord


def taxicab_voice_leading(chord_A: list, chord_B: list, enforce_parallel=True) -> list:
    while len(chord_A) < len(chord_B): chord_A.append(chord_A[-1])
    while len(chord_B) < len(chord_A): chord_B.append(chord_B[-1])
    chord_A = sorted(chord_A)
    best_routing, min_distance = None, float('inf')
    for perm_B in itertools.permutations(chord_B):
        dist = sum(abs(a - b) for a, b in zip(chord_A, perm_B))
        if enforce_parallel:
            for i in range(len(chord_A) - 1):
                if (chord_A[i + 1] - chord_A[i]) == 7 and (perm_B[i + 1] - perm_B[i]) == 7:
                    if (perm_B[i] - chord_A[i]) != 0: dist += 100
        if dist < min_distance:
            min_distance, best_routing = dist, perm_B
    return list(best_routing)


def leap_resolution(prev_note: int, current_note: int, next_note_target: int) -> int:
    leap = current_note - prev_note
    if abs(leap) > 5:
        direction = -1 if leap > 0 else 1
        return current_note + (direction * np.random.choice([1, 2]))
    return next_note_target


def infer_key_ks(midi_notes_with_durations: list):
    histogram = np.zeros(12)
    for pitch, duration in midi_notes_with_durations:
        histogram[pitch % 12] += duration
    if np.sum(histogram) > 0: histogram = histogram / np.sum(histogram)
    best_r, best_key = -1, (0, 'major')
    for key_type in ['major', 'minor']:
        base_profile = KK_PROFILES[key_type]
        for shift in range(12):
            rotated_profile = np.roll(base_profile, shift)
            r = np.corrcoef(histogram, rotated_profile)[0, 1]
            if r > best_r:
                best_r, best_key = r, (shift, key_type)
    return best_key


# ==========================================
# PART 3: RHYTHM & MICROTIMING
# ==========================================

def quantize_and_swing(tick_time: float, ppq: int = 480, swing_percent: float = 50.0) -> float:
    step_16th = ppq / 4.0
    quantized_time = round(tick_time / step_16th) * step_16th
    step_index = int(quantized_time / step_16th)
    if step_index % 2 != 0:
        shift_ticks = step_16th * ((swing_percent / 50.0) - 1.0)
        return quantized_time + shift_ticks
    return quantized_time


def apply_dilla_microtiming(instrument_type: str, beat_index: float, tick_time: float) -> float:
    if instrument_type == "kick":
        return tick_time
    elif instrument_type == "snare":
        if beat_index % 4 in [1.0, 3.0]: return tick_time + np.random.uniform(5, 15)
    elif instrument_type == "hihat":
        if np.random.choice(["heavy_swing", "rush"]) == "heavy_swing":
            return quantize_and_swing(tick_time, swing_percent=62.5)
        else:
            return tick_time - np.random.uniform(5, 10)
    return tick_time


def bjorklund_euclidean(k: int, n: int) -> list:
    if k == 0: return [0] * n
    if k == n: return [1] * n
    groups = [[1] for _ in range(k)]
    remainders = [[0] for _ in range(n - k)]
    while len(remainders) > 1:
        limit = min(len(groups), len(remainders))
        new_groups = [groups[i] + remainders[i] for i in range(limit)]
        remainders = groups[limit:] if len(groups) > limit else remainders[limit:]
        groups = new_groups
    pattern = []
    for g in groups: pattern.extend(g)
    for r in remainders: pattern.extend(r)
    return pattern


def rhythmic_regularity(d1: int, d2: int) -> float:
    if d1 == 0 or d2 == 0: return 0.0
    return math.gcd(d1, d2) / max(d1, d2)


# ==========================================
# PART 4: DYNAMICS, TENSION & ENTROPY
# ==========================================

def shannon_interval_entropy(midi_notes: list) -> float:
    if len(midi_notes) < 2: return 0.0
    intervals = np.diff(midi_notes)
    counts = Counter(intervals)
    entropy = sum(-(c / len(intervals)) * np.log2(c / len(intervals)) for c in counts.values())
    return entropy


def check_zipfian_distribution(midi_notes: list) -> bool:
    if len(midi_notes) < 2: return False
    counts = sorted(list(Counter(np.abs(np.diff(midi_notes))).values()), reverse=True)
    if len(counts) < 2: return False
    return 1.5 < (counts[0] / counts[1]) < 2.5


def apply_velocity_and_articulation(step_index: int, default_duration: float):
    base_vel = VELOCITY_GRID[step_index % 16]
    final_vel = int(np.clip(base_vel + np.random.normal(0, 5), 1, 127))
    duration_multiplier = np.random.uniform(1.0, 1.2) if step_index % 4 == 0 else np.random.uniform(0.5, 0.8)
    return final_vel, default_duration * duration_multiplier


def sigmoid_tension_multiplier(current_bar: int, total_bars: int = 16, k: float = 1.25, a: float = 10.0,
                               c: float = 0.75) -> float:
    t = current_bar / total_bars if total_bars > 0 else 0
    return 1.0 + ((k - 1.0) / (1.0 + np.exp(-a * (t - c))))


def calculate_arousal_tension(loudness_slope, pitch_height_slope, density_slope, roughness_slope):
    return (0.4 * loudness_slope) + (0.2 * pitch_height_slope) + (0.3 * density_slope) + (0.1 * roughness_slope)


# ==========================================
# PART 5: ARCHITECTURE & CC MAPPINGS
# ==========================================

def map_roughness_to_timbre(roughness_score: float, max_expected=10.0) -> dict:
    normalized = np.clip(roughness_score / max_expected, 0.0, 1.0)
    return {MIDI_CC['brightness']: int(127 - (normalized * 80)), MIDI_CC['resonance']: int(64 + (normalized * 63))}


def map_tension_to_cc(tension_multiplier: float) -> dict:
    return {MIDI_CC['expression']: int(np.clip(100 * tension_multiplier, 0, 127))}


def skyline_melody_node(polyphonic_events: list) -> list:
    time_map = {}
    for note, time in polyphonic_events:
        if time not in time_map or note > time_map[time]: time_map[time] = note
    return [(note, time) for time, note in time_map.items()]


def bass_identification_node(tracks: list) -> int:
    lowest_mean, bass_idx = float('inf'), 0
    for idx, track in enumerate(tracks):
        if not track: continue
        mean_pitch = np.mean([n for n, t in track])
        if mean_pitch < lowest_mean: lowest_mean, bass_idx = mean_pitch, idx
    return bass_idx


def extract_and_align_voices(events, is_converging=False, max_polyphony=4):
    """
    LEVEL 3 & 4: Separates voices, enforces max polyphony, and aligns vertical harmony.
    """
    # Group events by their quantized start times to find simultaneous chords/clusters
    time_clusters = {}
    for e in events:
        t = round(e['time'] / 120) * 120  # Group by 16th note grid approx
        if t not in time_clusters: time_clusters[t] = []
        time_clusters[t].append(e)

    melody_events, bass_events, inner_events = [], [], []

    for t, cluster in time_clusters.items():
        if not cluster: continue
        cluster.sort(key=lambda x: x['pitch'])

        # ==========================================
        # LEVEL 3: DENSITY & POLYPHONY PRUNING
        # ==========================================
        if len(cluster) > max_polyphony:
            bass = cluster[0]
            melody = cluster[-1]
            # Keep evenly spaced inner voices, drop the rest
            allowed_inner = max_polyphony - 2
            inner = cluster[1: 1 + allowed_inner]
            pruned_cluster = [bass] + inner + [melody]
        else:
            pruned_cluster = cluster

        # ==========================================
        # LEVEL 4: HARMONY ALIGNMENT & BASS FORCING
        # ==========================================
        if is_converging and len(pruned_cluster) >= 3:
            pitches = [e['pitch'] for e in pruned_cluster]
            chord_info = infer_chord(pitches)

            if chord_info:
                root_pc, chord_name = chord_info

                # 1. Force Bass to Chord Root
                bass_event = pruned_cluster[0]
                bass_octave = (bass_event['pitch'] // 12) * 12
                bass_event['pitch'] = bass_octave + root_pc

                # 2. Snap Inner Voices strictly to the chord template
                template = CHORD_TEMPLATES[chord_name]
                allowed_pcs = [(root_pc + i) % 12 for i in template]

                for note in pruned_cluster[1:-1]:
                    pc = note['pitch'] % 12
                    if pc not in allowed_pcs:
                        # Find nearest chord tone
                        best_shift, min_dist = 0, 100
                        for offset in range(-6, 7):
                            if (pc + offset) % 12 in allowed_pcs and abs(offset) < min_dist:
                                min_dist, best_shift = abs(offset), offset
                        note['pitch'] += best_shift

        # Append to respective streams
        bass_events.append(pruned_cluster[0])
        if len(pruned_cluster) > 1:
            melody_events.append(pruned_cluster[-1])
            for inner in pruned_cluster[1:-1]:
                inner_events.append(inner)

    return bass_events, inner_events, melody_events


def align_procedural_midi(raw_events: list,
                          genre: str = "Lo-Fi",
                          ppq: int = 480,
                          iteration_depth: int = 0,
                          global_key=None,
                          show_step: bool = True):
    profile = GENRE_PROFILES[genre]

    # 1. Global Targets (Determined once and passed down)
    if global_key is None:
        pitch_durs = [(e['pitch'], e['duration']) for e in raw_events]
        implied_key = infer_key_ks(pitch_durs)
        log_step(f"Inferred Musical Key: Root {implied_key[0]}, Scale {implied_key[1]}", show_step)
    else:
        implied_key = global_key

    # Define convergence phase
    is_converging = iteration_depth > 1
    if is_converging:
        log_step("Convergence Phase: Locking voices and removing stochastic jitter.", show_step)
        scale_strictness = 1.0
        weirdness_ratio = 0.0
        apply_humanization = False
    else:
        scale_strictness = profile["scale_strictness"]
        weirdness_ratio = 1.0 - profile["scale_strictness"]
        apply_humanization = True

    # Buffers
    aligned_events = []
    aligned_events_per_stream = {"bass": [], "inner": [], "melody": []}

    # Set Polyphony based on Genre (fallback to 4 if not defined)
    max_polyphony = profile.get("max_polyphony", 4)
    if genre in ["Jazz", "Ambient"]: max_polyphony = 6
    if genre in ["Trap", "EDM"]: max_polyphony = 3

    # 2. Extract stable voices, prune density, and force chords
    bass_stream, inner_stream, melody_stream = extract_and_align_voices(
        raw_events,
        is_converging=is_converging,
        max_polyphony=max_polyphony
    )

    all_streams = [("bass", bass_stream), ("inner", inner_stream), ("melody", melody_stream)]

    for stream_name, stream in all_streams:
        for i, event in enumerate(stream):
            aligned = event.copy()
            step_16th = ppq / 4.0
            step_index = int(aligned['time'] / step_16th)

            # Rhythmic Quantize
            if profile["quantize_strictness"] > 0:
                aligned['time'] = quantize_and_swing(aligned['time'], ppq, profile['swing_percent'])
                if apply_humanization and genre == "Lo-Fi":
                    aligned['time'] = apply_dilla_microtiming(
                        aligned.get('instrument', 'hihat'),
                        step_index / 4.0,
                        aligned['time']
                    )

            # Pitch / Scale constraints
            if np.random.random() < scale_strictness:
                aligned['pitch'] = snap_to_scale(aligned['pitch'], implied_key[0], implied_key[1])

            # Deterministic Voice Leading (Only in convergence phase)
            if is_converging and aligned_events_per_stream[stream_name]:
                prev_pitch = aligned_events_per_stream[stream_name][-1]['pitch']
                if stream_name == 'melody':
                    aligned['pitch'] = leap_resolution(prev_pitch, aligned['pitch'], aligned['pitch'])
                elif stream_name == 'bass' and aligned['pitch'] > 60:
                    aligned['pitch'] -= 12

            # Dynamics
            vel, dur = apply_velocity_and_articulation(step_index, aligned['duration'])
            if not apply_humanization:
                vel = VELOCITY_GRID[step_index % 16]

            bar_idx = aligned['time'] / (ppq * 4)
            tension_mod = sigmoid_tension_multiplier(bar_idx, total_bars=16)
            aligned['velocity'] = int(np.clip(vel * tension_mod, 1, 127))
            aligned['duration'] = dur

            aligned_events.append(aligned)
            aligned_events_per_stream[stream_name].append(aligned)

    # Re-sort events chronologically after combining streams
    aligned_events.sort(key=lambda x: x['time'])

    # Low Interval Limit Check
    for i in range(len(aligned_events) - 1):
        if aligned_events[i]['pitch'] < 48:
            pair = enforce_low_interval_limits([aligned_events[i]['pitch'],
                                                aligned_events[i + 1]['pitch']])
            aligned_events[i]['pitch'] = pair[0]

    # 3. Weirdness Preservation (Only active in early passes)
    if not is_converging and weirdness_ratio > 0:
        final_events = protect_weirdness(raw_events, aligned_events,
                                         protection_percentage=weirdness_ratio)
    else:
        final_events = aligned_events

    return final_events, implied_key



def apply_oblique_strategies(midi_sequence: list, strategy="random"):
    chosen = strategy if strategy in ["retrograde", "extreme_transposition"] else np.random.choice(
        ["retrograde", "extreme_transposition"])
    if chosen == "retrograde": return midi_sequence[::-1]
    return [{**e, 'pitch': min(127, e['pitch'] + 36)} for e in midi_sequence]


def protect_weirdness(raw_events: list, aligned_events: list, protection_percentage: float = 0.10) -> list:
    return [raw if np.random.random() < protection_percentage else aligned for raw, aligned in
            zip(raw_events, aligned_events)]


# ==========================================
# I/O: MIDI FILE PARSER & COMPILER
# ==========================================

def read_midi_to_events(file_path):
    mid = mido.MidiFile(file_path)
    ppq = mid.ticks_per_beat
    events = []
    meta_events = []  # Store program changes, tempo, CCs, names, etc.

    for i, track in enumerate(mid.tracks):
        abs_time = 0
        active_notes = {}  # {note: start_data}

        for msg in track:
            abs_time += msg.time

            # Handle Note On
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = {
                    'time': abs_time,
                    'velocity': msg.velocity,
                    'channel': getattr(msg, 'channel', 0)
                }
            # Handle Note Off
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start_data = active_notes.pop(msg.note)
                    events.append({
                        'pitch': msg.note,
                        'velocity': start_data['velocity'],
                        'time': start_data['time'],
                        'duration': abs_time - start_data['time'],
                        'track_idx': i,
                        'channel': start_data['channel'],
                        'instrument': 'synth'  # Placeholder for alignment logic
                    })
            # Handle EVERYTHING ELSE (Program changes/instruments, Tempo, Control Changes)
            else:
                meta_events.append({
                    'track_idx': i,
                    'time': abs_time,
                    'msg': msg.copy(time=0)  # Strip delta time, we will recalculate it on export
                })

    events.sort(key=lambda x: x['time'])
    return events, meta_events, ppq, mid


def write_events_to_midi(events, meta_events, ppq, output_path):
    out_mid = mido.MidiFile(ticks_per_beat=ppq)

    # Determine how many tracks we need safely
    max_track_events = max([e['track_idx'] for e in events] + [0])
    max_track_meta = max([e['track_idx'] for e in meta_events] + [0])
    track_count = max(max_track_events, max_track_meta) + 1

    tracks = [mido.MidiTrack() for _ in range(track_count)]
    for trk in tracks:
        out_mid.tracks.append(trk)

    # Flatten aligned notes into note_on and note_off
    flat_msgs = []
    for e in events:
        chan = e.get('channel', 0)
        # Ensure pitches and velocities are cast to ints so mido doesn't crash
        pitch = int(np.clip(e['pitch'], 0, 127))
        vel = int(np.clip(e['velocity'], 0, 127))

        flat_msgs.append({
            'is_note': True, 'type': 'note_on', 'note': pitch, 'velocity': vel,
            'time': e['time'], 'track': e['track_idx'], 'channel': chan
        })
        flat_msgs.append({
            'is_note': True, 'type': 'note_off', 'note': pitch, 'velocity': 0,
            'time': e['time'] + e['duration'], 'track': e['track_idx'], 'channel': chan
        })

    # Inject preserved meta & control events (like instruments)
    for me in meta_events:
        flat_msgs.append({
            'is_note': False,
            'msg': me['msg'],
            'time': me['time'],
            'track': me['track_idx']
        })

    # Sort messages per track by absolute time
    track_msgs = {i: [] for i in range(track_count)}
    for msg in flat_msgs:
        track_msgs[msg['track']].append(msg)

    for trk_idx, msgs in track_msgs.items():
        # Sort primarily by time.
        # Tie-breaker: put non-notes (meta/program_change) BEFORE notes if they land on the same tick!
        msgs.sort(key=lambda x: (x['time'], 1 if x.get('is_note') else 0))

        last_time = 0
        for msg in msgs:
            delta = int(max(0, msg['time'] - last_time))
            if not msg.get('is_note'):
                # Apply the newly calculated delta to the preserved metadata
                new_msg = msg['msg'].copy(time=delta)
                tracks[trk_idx].append(new_msg)
            else:
                tracks[trk_idx].append(mido.Message(
                    msg['type'], note=msg['note'], velocity=msg['velocity'],
                    time=delta, channel=msg['channel']
                ))
            last_time = msg['time']

    out_mid.save(output_path)


def process_midi_file(input_file: str,
                      genre: str = "Lo-Fi",
                      iterations: int = 3,
                      show_step: bool = True):
    setup_logging()

    log_step(f"Opening input file: {input_file}", show_step)
    if not os.path.exists(input_file):
        log_step(f"Error: {input_file} not found.", show_step)
        return

    base_name = os.path.splitext(os.path.basename(input_file))[0]
    os.makedirs("Restructured", exist_ok=True)
    output_file = f"Restructured/{base_name}_restructured.mid"
    graph_file = f"Restructured/{base_name}_trajectory.png"

    # 1. Parse Input
    raw_events, meta_events, ppq, original_mid = read_midi_to_events(input_file)
    log_step(f"Parsed {len(raw_events)} notes and {len(meta_events)} metadata/control events. PPQ is {ppq}.", show_step)

    if not raw_events:
        log_step("No notes found in MIDI file.", show_step)
        return

    # 2. Execute Align Engine iteratively (with convergence)
    history = [raw_events]
    current_events = raw_events
    global_key = None

    log_step(f"Running Alignment Engine for {iterations} iteration(s)...", show_step)
    for i in range(iterations):
        log_step(f"\n--- ITERATION {i+1}/{iterations} ---", show_step)
        current_events, global_key = align_procedural_midi(
            current_events,
            genre=genre,
            ppq=ppq,
            iteration_depth=i,
            global_key=global_key,
            show_step=show_step
        )
        history.append([e.copy() for e in current_events])

    # 3. (Optional) plot note trajectories if you kept plot_note_changes
    try:
        plot_note_changes(history, graph_file)
        log_step(f"Saved pitch trajectory graph to: {graph_file}", show_step)
    except NameError:
        pass

    # 4. Export Output
    write_events_to_midi(current_events, meta_events, ppq, output_file)
    log_step(f"Successfully saved output to: {output_file}", show_step)
    log_step(f"Log written to: {log_file_path}", show_step)



def write_events_to_midi(events, meta_events, ppq, output_path):
    # Force PPQ to a native python integer
    out_mid = mido.MidiFile(ticks_per_beat=int(ppq))

    # Determine how many tracks we need safely
    max_track_events = int(max([e['track_idx'] for e in events] + [0]))
    max_track_meta = int(max([e['track_idx'] for e in meta_events] + [0]))
    track_count = max(max_track_events, max_track_meta) + 1

    tracks = [mido.MidiTrack() for _ in range(track_count)]
    for trk in tracks:
        out_mid.tracks.append(trk)

    # Flatten aligned notes into note_on and note_off
    flat_msgs = []
    for e in events:
        chan = int(e.get('channel', 0))
        pitch = int(np.clip(e['pitch'], 0, 127))
        vel = int(np.clip(e['velocity'], 0, 127))
        start_time = float(e['time'])
        end_time = float(e['time'] + e['duration'])

        flat_msgs.append({
            'is_note': True, 'type': 'note_on', 'note': pitch, 'velocity': vel,
            'time': start_time, 'track': int(e['track_idx']), 'channel': chan
        })
        flat_msgs.append({
            'is_note': True, 'type': 'note_off', 'note': pitch, 'velocity': 0,
            'time': end_time, 'track': int(e['track_idx']), 'channel': chan
        })

    # Inject preserved meta & control events (like instruments)
    for me in meta_events:
        flat_msgs.append({
            'is_note': False,
            'msg': me['msg'],
            'time': float(me['time']),
            'track': int(me['track_idx'])
        })

    # Sort messages per track by absolute time
    track_msgs = {i: [] for i in range(track_count)}
    for msg in flat_msgs:
        track_msgs[msg['track']].append(msg)

    for trk_idx, msgs in track_msgs.items():
        # Sort primarily by time.
        # Tie-breaker: put non-notes (meta/program_change) BEFORE notes if they land on the same tick
        msgs.sort(key=lambda x: (x['time'], 1 if x.get('is_note') else 0))

        last_time = 0.0
        for msg in msgs:
            # Calculate delta time, round it, and FORCE native int
            delta = int(round(max(0.0, msg['time'] - last_time)))

            if not msg.get('is_note'):
                # Apply the newly calculated delta to the preserved metadata
                new_msg = msg['msg'].copy(time=delta)
                tracks[trk_idx].append(new_msg)
            else:
                tracks[trk_idx].append(mido.Message(
                    msg['type'],
                    note=int(msg['note']),
                    velocity=int(msg['velocity']),
                    time=delta,
                    channel=int(msg['channel'])
                ))
            last_time = msg['time']

    out_mid.save(output_path)


# ==========================================
# GRAPHING: VISUALIZE NOTE TRAJECTORIES
# ==========================================
def plot_note_changes(history, output_path):
    if not MATPLOTLIB_AVAILABLE:
        print("[ALIGNER] Matplotlib not installed. Skipping graph generation. (Run: pip install matplotlib)")
        return

    plt.figure(figsize=(14, 7))

    # We will track the first 100 notes to prevent the graph from becoming an unreadable blob
    num_notes_to_track = min(100, len(history[0]))

    for note_idx in range(num_notes_to_track):
        pitches = []
        for iteration_events in history:
            if note_idx < len(iteration_events):
                pitches.append(iteration_events[note_idx]['pitch'])
            else:
                pitches.append(None)  # In case notes get pruned in future updates

        # Plot the trajectory of this specific note across iterations
        plt.plot(range(len(history)), pitches, marker='o', markersize=4, alpha=0.4, linewidth=1.5)

    plt.title(f"Pitch Trajectory of Notes Across Alignment Iterations", fontsize=14, fontweight='bold')
    plt.xlabel("Iteration (0 = Original Noise Seed)", fontsize=12)
    plt.ylabel("MIDI Pitch (0 - 127)", fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.xticks(range(len(history)))

    # Highlight normal musical ranges
    plt.axhspan(36, 84, color='green', alpha=0.05, label='Standard Musical Register (C2 - C6)')
    plt.legend(loc="upper right")

    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()


def process_midi_file(input_file: str, genre: str = "Lo-Fi", iterations: int = 1, show_step: bool = True):
    setup_logging()

    log_step(f"Opening input file: {input_file}", show_step)
    if not os.path.exists(input_file):
        log_step(f"Error: {input_file} not found.", show_step)
        return

    base_name = os.path.splitext(input_file)[0]
    output_file = f"{base_name}_restructured.mid"
    graph_file = f"{base_name}_trajectory.png"

    # 1. Parse Input
    raw_events, meta_events, ppq, original_mid = read_midi_to_events(input_file)
    log_step(f"Parsed {len(raw_events)} notes and {len(meta_events)} metadata/control events. PPQ is {ppq}.", show_step)

    if not raw_events:
        log_step("No notes found in MIDI file.", show_step)
        return

    # 2. Execute Align Engine Iteratively
    history = [raw_events]
    current_events = raw_events
    global_key = None

    log_step(f"Running Alignment Engine for {iterations} iteration(s)...", show_step)
    for i in range(iterations):
        log_step(f"\n--- ITERATION {i + 1}/{iterations} ---", show_step)

        # Pass the iteration depth and the locked key
        current_events, global_key = align_procedural_midi(
            current_events,
            genre=genre,
            ppq=ppq,
            iteration_depth=i,
            global_key=global_key,
            show_step=show_step
        )

        # Deep copy to ensure Matplotlib history graph tracks correctly
        history.append([e.copy() for e in current_events])

    # 3. Graph the Changes
    log_step(f"\nGenerating Pitch Trajectory Graph...", show_step)
    plot_note_changes(history, graph_file)
    log_step(f"Saved graph to: {graph_file}", show_step)

    # 4. Export Output
    write_events_to_midi(current_events, meta_events, ppq, output_file)
    log_step(f"Successfully saved MIDI output to: {output_file}", show_step)
    log_step(f"Log written to: {log_file_path}", show_step)


# ==========================================
# CLI ENTRY POINT WITH INTERACTIVE FALLBACK
# ==========================================
if __name__ == "__main__":
    import argparse
    import sys

    if len(sys.argv) == 1:
        print("\n=== HVMIDI Aligner : Interactive Mode ===")

        input_file = ""
        while not os.path.exists(input_file):
            input_file = input("Enter the path to the raw MIDI file: ").strip().strip("'\"")
            if not os.path.exists(input_file):
                print(f"Error: File '{input_file}' not found. Please try again.")

        valid_genres = list(GENRE_PROFILES.keys())
        genre_input = input(f"Select a genre profile ({', '.join(valid_genres)}) [Default: Lo-Fi]: ").strip()
        genre_match = next((g for g in valid_genres if g.lower() == genre_input.lower()), None)
        genre = genre_match if genre_match else "Lo-Fi"

        iter_input = input("How many alignment iterations should be applied? [Default: 3]: ").strip()
        try:
            iterations = int(iter_input) if iter_input else 3
        except ValueError:
            iterations = 3

        silent_input = input("Enable silent mode? (hide console step output) [y/N]: ").strip().lower()
        show_step = not (silent_input == 'y' or silent_input == 'yes')

        print("\nStarting process...\n" + "=" * 40)
        process_midi_file(input_file, genre=genre, iterations=iterations, show_step=show_step)

    else:
        parser = argparse.ArgumentParser(description="HVMIDI Aligner - Procedural MIDI Restructuring Engine")
        parser.add_argument("input", help="Path to the raw MIDI file")
        parser.add_argument("--genre", type=str, default="Lo-Fi", choices=GENRE_PROFILES.keys(),
                            help="Musical genre profile")
        parser.add_argument("--iter", type=int, default=3, help="Number of times to run the sequence")
        parser.add_argument("--silent", action="store_true", help="Disable console step output")

        args = parser.parse_args()
        process_midi_file(args.input, genre=args.genre, iterations=args.iter, show_step=not args.silent)
