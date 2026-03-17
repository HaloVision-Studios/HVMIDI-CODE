import math
import random
import base64
import gzip
import os
import hashlib
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import platform
import mido
from mido import Message, MidiFile, MidiTrack, MetaMessage


CAN_EXPORT_AUDIO = True
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "Static")
SOUNDFONT_PATH = os.path.join(BASE_DIR, "default.sf2")

# =====================================================================
# CONFIGURATION
# =====================================================================
# Get the absolute directory where halovision.py is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Update this path to a valid .sf2 SoundFont file on your system
# This forces python to look exactly next to halovision.py
# SOUNDFONT_PATH = os.path.join(SCRIPT_DIR, "default.sf2")


# =====================================================================
# 0A. COORD <-> HVCOORD STRING (PROCEDURAL SEEDS)
# =====================================================================

def coord_to_hvcoord(coord: str) -> str:
    """
    Deterministically encode a coordinate string into an HVCOORD- seed
    using gzip (with fixed mtime) + base64.
    """
    compressed = gzip.compress(coord.encode("utf-8"), mtime=0)
    b64 = base64.b64encode(compressed).decode("utf-8")
    return "HVCOORD-" + b64


def hvcoord_to_coord(hvcoord: str) -> str:
    """
    Decode an HVCOORD- seed back into its coordinate string.
    """
    if not hvcoord.startswith("HVCOORD-"):
        raise ValueError("Not an HVCOORD seed (missing 'HVCOORD-' prefix).")
    b64 = hvcoord[len("HVCOORD-"):]
    compressed = base64.b64decode(b64)
    coord = gzip.decompress(compressed).decode("utf-8")
    return coord


# =====================================================================
# 0B. MIDI <-> HVMIDI STRING (1:1 RAW MIDI)
# =====================================================================

def midi_to_hvmidi(path: str) -> str:
    """
    Encode an existing MIDI file into an HVMIDI- seed (1:1 bytes).
    """
    with open(path, "rb") as f:
        raw = f.read()
    compressed = gzip.compress(raw, mtime=0)
    b64 = base64.b64encode(compressed).decode("utf-8")
    return "HVMIDI-" + b64


def hvmidi_to_midi(hvmidi: str, out_path: str):
    """
    Decode an HVMIDI- seed back into its exact original MIDI bytes.
    """
    if not hvmidi.startswith("HVMIDI-"):
        raise ValueError("Not an HVMIDI seed.")
    b64 = hvmidi[len("HVMIDI-"):]
    compressed = base64.b64decode(b64)
    raw = gzip.decompress(compressed)
    with open(out_path, "wb") as f:
        f.write(raw)
    print(f"\n[SUCCESS] 1:1 MIDI reconstructed to {out_path}")

# =====================================================================
# 1. PROCEDURAL ENGINE SEED STRUCTURES & DYNAMIC PARSER
# =====================================================================

@dataclass
class SeedBundle:
    master_seed: int
    form_seed: Optional[int] = None
    harmony_seed: Optional[int] = None
    modulation_seed: Optional[int] = None
    rhythm_seed: Optional[int] = None
    melody_seed: Optional[int] = None
    instrument_seed: Optional[int] = None
    arrangement_seed: Optional[int] = None
    expression_seed: Optional[int] = None
    ornament_seed: Optional[int] = None
    mix_seed: Optional[int] = None
    groove_seed: Optional[int] = None

    def finalize(self):
        for name in [
            "form_seed", "harmony_seed", "modulation_seed", "rhythm_seed",
            "melody_seed", "instrument_seed", "arrangement_seed",
            "expression_seed", "ornament_seed", "mix_seed", "groove_seed",
        ]:
            if getattr(self, name) is None:
                setattr(self, name, derive_seed(self.master_seed, name))

@dataclass
class HVConfig:
    seeds: SeedBundle
    target_duration_seconds: float = 60.0
    instrument_count: Optional[int] = None
    bpm: Optional[int] = None
    ppq: int = 480

    # Default traits (will be overwritten by AI parameters)
    tempo_range: Tuple[int, int] = (40, 240)
    density_mult: float = 1.0
    pitch_offset: int = 0
    drum_bias: float = 0.15
    polyphony_max: int = 6
    articulation_mult: float = 1.0

def derive_seed(master: int, label: str) -> int:
    stable_label_hash = int(hashlib.sha256(label.encode("utf-8")).hexdigest()[:8], 16)
    return (master + stable_label_hash) & 0xFFFFFFFF

def parse_coord_to_parameters(coord_string: str, cfg: HVConfig):
    """
    Reads the AI-generated coordinate string and extracts the exact numerical parameters.
    Expected format: Store=MyDream;T=50-90;Den=0.8;P=-12;Dr=0.2;Poly=5;Art=4.0;Inst=6
    """
    parts = {}
    for seg in coord_string.split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            parts[k.strip()] = v.strip()

    try:
        if "T" in parts and "-" in parts["T"]:
            mn, mx = parts["T"].split("-")
            cfg.tempo_range = (int(mn), int(mx))
        if "Den" in parts: cfg.density_mult = float(parts["Den"])
        if "P" in parts: cfg.pitch_offset = int(parts["P"])
        if "Dr" in parts: cfg.drum_bias = float(parts["Dr"])
        if "Poly" in parts: cfg.polyphony_max = int(parts["Poly"])
        if "Art" in parts: cfg.articulation_mult = float(parts["Art"])
        if "Inst" in parts: cfg.instrument_count = int(parts["Inst"])
    except ValueError:
        pass # If the AI hallucinates bad math, ignore and use defaults
@dataclass
class TrackProfile:
    id: int
    is_drum: bool
    channel: int
    program: int
    bank_msb: int
    bank_lsb: int
    polyphony: int
    pitch_center: int
    pitch_spread: int
    grid_pool: List[float]
    base_density: float
    base_articulation: float

@dataclass
class SectionProfile:
    start_tick: int
    end_tick: int
    pitch_classes: List[int]
    active_tracks: set

# =====================================================================
# 2. COORDINATE -> SEEDBUNDLE (64-bit per sub-seed)
# =====================================================================

def stable_hash64(s: str) -> int:
    """Stable 64‑bit integer from any string using sha256."""
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:16], 16)  # 16 hex chars = 64 bits


def coord_to_seeds(coord: str) -> SeedBundle:
    """
    Map a coordinate string to a SeedBundle.
    """
    parts = {}
    for seg in coord.split(";"):
        if "=" not in seg:
            continue
        k, v = seg.split("=", 1)
        parts[k.strip()] = v.strip()

    store = parts.get("Store", "HaloVision")
    room = parts.get("Room", "Main")
    rack = parts.get("Rack", "1")
    crate = parts.get("Crate", "1")
    sleeve = parts.get("Sleeve", "1")

    m_seed = stable_hash64(f"{store}")
    h_seed = stable_hash64(f"{store}:{room}")
    i_seed = stable_hash64(f"{store}:{room}:{rack}")
    r_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}")
    g_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:groove")
    mel_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:{sleeve}")
    f_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:{sleeve}:form")
    e_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:{sleeve}:expr")
    o_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:{sleeve}:orn")
    x_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:{sleeve}:mix")
    mod_seed = stable_hash64(f"{store}:{room}:{rack}:{crate}:{sleeve}:mod")

    return SeedBundle(
        master_seed=m_seed,
        harmony_seed=h_seed,
        instrument_seed=i_seed,
        rhythm_seed=r_seed,
        groove_seed=g_seed,
        melody_seed=mel_seed,
        form_seed=f_seed,
        expression_seed=e_seed,
        ornament_seed=o_seed,
        mix_seed=x_seed,
        modulation_seed=mod_seed,
    )


# =====================================================================
# 3. GENERATOR IMPLEMENTATION (with tempo / pitchbend / CCs)
# =====================================================================

def generate_tracks(cfg: HVConfig) -> List[TrackProfile]:
    rng = random.Random(cfg.seeds.instrument_seed)
    rhythm_rng = random.Random(cfg.seeds.rhythm_seed)

    count = cfg.instrument_count if cfg.instrument_count is not None else rng.randint(1, 16)
    count = max(1, min(count, 16))

    available_channels = [ch for ch in range(16) if ch != 9]
    rng.shuffle(available_channels)

    tracks: List[TrackProfile] = []
    for i in range(count):
        # Use cfg.drum_bias instead of hardcoded 0.15
        is_drum = (rng.random() < cfg.drum_bias) or (i == 0 and rng.random() < 0.5)

        if is_drum:
            channel = 9
            program = rng.randint(0, 127)
            bank_msb = 120
            bank_lsb = rng.randint(0, 127)
            pitch_center = 45
            pitch_spread = 20
            polyphony = rng.randint(1, 4)
        else:
            channel = available_channels.pop() if available_channels else 0
            program = rng.randint(0, 127)
            bank_msb = rng.choice([0, 121])
            bank_lsb = rng.randint(0, 127)
            # Apply pitch offset from mood
            pitch_center = max(20, min(100, rng.randint(30, 90) + cfg.pitch_offset))
            pitch_spread = rng.randint(5, 36)
            # Cap polyphony based on texture
            max_poly = min(6, cfg.polyphony_max)
            poly_weights = [50, 20, 15, 10, 5][:max_poly]
            poly_choices = [1, 2, 3, 4, 6][:max_poly]
            polyphony = rng.choices(poly_choices, weights=poly_weights)[0]

        grid_type = rhythm_rng.random()
        if grid_type < 0.6:
            grid_pool = rhythm_rng.choice([
                [0.25, 0.5, 1.0], [0.5, 1.0, 2.0], [0.125, 0.25], [0.333, 0.666, 1.0], [1.5, 0.75],
            ])
        elif grid_type < 0.9:
            grid_pool = [rhythm_rng.choice([0.2, 0.4, 0.6, 0.8, 1.2, 2.5, 5.0]) for _ in
                         range(rhythm_rng.randint(2, 5))]
        else:
            grid_pool = [rhythm_rng.uniform(0.05, 4.0) for _ in range(rhythm_rng.randint(3, 10))]

        # Apply density and articulation multipliers from catalogue
        density = rhythm_rng.uniform(0.1, 0.95) * cfg.density_mult
        density = max(0.05, min(1.0, density))

        articulation = rhythm_rng.choice([0.1, 0.5, 0.9, 1.0, 1.5, 3.0]) * cfg.articulation_mult

        tracks.append(TrackProfile(
            id=i, is_drum=is_drum, channel=channel, program=program,
            bank_msb=bank_msb, bank_lsb=bank_lsb, polyphony=polyphony,
            pitch_center=pitch_center, pitch_spread=pitch_spread,
            grid_pool=grid_pool, base_density=density, base_articulation=articulation
        ))

    return tracks


def generate_sections_and_harmony(cfg: HVConfig,
                                  total_ticks: int,
                                  tracks: List[TrackProfile]) -> List[SectionProfile]:
    form_rng = random.Random(cfg.seeds.form_seed)
    harm_rng = random.Random(cfg.seeds.harmony_seed)
    arr_rng = random.Random(cfg.seeds.arrangement_seed)

    num_sections = form_rng.randint(1, 8)
    splits = sorted([form_rng.randint(0, total_ticks) for _ in range(num_sections - 1)])
    boundaries = [0] + splits + [total_ticks]

    sections: List[SectionProfile] = []
    for i in range(num_sections):
        start_t = boundaries[i]
        end_t = boundaries[i + 1]
        if end_t - start_t < cfg.ppq:
            continue

        num_pcs = harm_rng.randint(1, 12)
        pitch_classes = harm_rng.sample(range(12), num_pcs)

        active_tracks = set()
        for t in tracks:
            if arr_rng.random() < 0.80:
                active_tracks.add(t.id)
        if not active_tracks and tracks:
            active_tracks.add(arr_rng.choice(tracks).id)

        sections.append(SectionProfile(
            start_tick=start_t, end_tick=end_t,
            pitch_classes=pitch_classes, active_tracks=active_tracks
        ))

    return sections


def generate_all_events(cfg: HVConfig,
                        tracks: List[TrackProfile],
                        sections: List[SectionProfile]):
    melody_rng = random.Random(cfg.seeds.melody_seed)
    rhythm_rng = random.Random(cfg.seeds.rhythm_seed)
    expr_rng = random.Random(cfg.seeds.expression_seed)
    ornament_rng = random.Random(cfg.seeds.ornament_seed)
    groove_rng = random.Random(cfg.seeds.groove_seed)

    all_events: Dict[int, List[Tuple[int, int, int]]] = {t.id: [] for t in tracks}

    for sec in sections:
        for t in tracks:
            if t.id not in sec.active_tracks:
                continue

            time_cursor = sec.start_tick
            while time_cursor < sec.end_tick:
                step_beats = rhythm_rng.choice(t.grid_pool)
                step_ticks = max(1, int(step_beats * cfg.ppq))

                if rhythm_rng.random() < t.base_density:
                    notes_to_play = melody_rng.randint(1, t.polyphony)
                    length_ticks = int(step_ticks * t.base_articulation)
                    if ornament_rng.random() < 0.1:
                        length_ticks = int(length_ticks * ornament_rng.uniform(0.1, 3.0))
                    length_ticks = max(1, length_ticks)

                    for _ in range(notes_to_play):
                        if t.is_drum:
                            note = melody_rng.randint(35, 81)
                        else:
                            pc = melody_rng.choice(sec.pitch_classes)
                            offset = melody_rng.randint(-t.pitch_spread, t.pitch_spread)
                            note = t.pitch_center + offset
                            note = ((note // 12) * 12) + pc
                        note = max(0, min(127, note))
                        velocity = expr_rng.randint(20, 127)

                        jitter = groove_rng.uniform(-0.1, 0.1) * cfg.ppq
                        start_ticks = time_cursor + int(jitter)
                        end_ticks = start_ticks + length_ticks

                        all_events[t.id].append((start_ticks, note, velocity))
                        all_events[t.id].append((end_ticks, note, 0))

                time_cursor += step_ticks

    return all_events


def generate_cc_curves(cfg: HVConfig,
                       tracks: List[TrackProfile],
                       total_ticks: int):
    rng_expr = random.Random(cfg.seeds.expression_seed)
    rng_mix = random.Random(cfg.seeds.mix_seed)

    curves: Dict[int, List[Tuple[int, List[Message]]]] = {t.id: [] for t in tracks}

    for t in tracks:
        base_vol = rng_expr.randint(40, 127)
        pan_center = rng_mix.randint(0, 127)
        base_expr = rng_expr.randint(60, 127)
        base_mod = rng_expr.randint(0, 127)
        base_bright = rng_expr.randint(0, 127)

        steps = rng_mix.randint(1, 20)
        step_ticks = total_ticks // steps if steps > 0 else total_ticks

        events = []
        for i in range(steps + 1):
            tick = min(i * step_ticks, total_ticks)
            vol = max(10, min(127, base_vol + rng_expr.randint(-30, 30)))
            pan = max(0, min(127, pan_center + rng_mix.randint(-64, 64)))
            expr_val = max(0, min(127, base_expr + rng_expr.randint(-20, 20)))
            mod_val = max(0, min(127, base_mod + rng_expr.randint(-40, 40)))
            bright_val = max(0, min(127, base_bright + rng_expr.randint(-40, 40)))

            msgs = [
                Message('control_change', control=7, value=vol, channel=t.channel, time=0),
                Message('control_change', control=10, value=pan, channel=t.channel, time=0),
                Message('control_change', control=11, value=expr_val, channel=t.channel, time=0),
                Message('control_change', control=1, value=mod_val, channel=t.channel, time=0),
                Message('control_change', control=74, value=bright_val, channel=t.channel, time=0),
            ]
            events.append((tick, msgs))

        curves[t.id] = events

    return curves


def generate_pitch_bend_curves(cfg: HVConfig,
                               tracks: List[TrackProfile],
                               total_ticks: int):
    rng = random.Random(cfg.seeds.modulation_seed)
    curves: Dict[int, List[Tuple[int, Message]]] = {t.id: [] for t in tracks}

    for t in tracks:
        if rng.random() < 0.5:
            continue

        steps = rng.randint(2, 20)
        step_ticks = total_ticks // steps if steps > 0 else total_ticks
        range_semitones = rng.choice([1, 2, 5, 12])

        for i in range(steps + 1):
            tick = min(i * step_ticks, total_ticks)

            delta_semi = rng.uniform(-range_semitones, range_semitones)
            bend_val = int(8192 + (delta_semi / range_semitones) * 4096)
            bend_val = max(0, min(16383, bend_val))

            pitch = bend_val - 8192
            if pitch < -8192:
                pitch = -8192
            elif pitch > 8191:
                pitch = 8191

            msg = Message('pitchwheel', channel=t.channel, pitch=pitch, time=0)
            curves[t.id].append((tick, msg))

    return curves


def build_tempo_map(cfg: HVConfig, total_ticks: int):
    rng = random.Random(cfg.seeds.modulation_seed)
    tempo_events = []

    changes = rng.randint(0, 8)
    if changes == 0:
        bpm = rng.randint(40, 240)
        tempo_events.append((0, bpm))
        return tempo_events

    current_bpm = rng.randint(40, 240)
    tempo_events.append((0, current_bpm))

    for _ in range(changes):
        tick = rng.randint(0, total_ticks)
        delta = rng.randint(-60, 60)
        current_bpm = max(30, min(300, current_bpm + delta))
        tempo_events.append((tick, current_bpm))

    tempo_events.sort(key=lambda x: x[0])
    return tempo_events


# =====================================================================
# 4. EXPORT FUNCTIONS
# =====================================================================

def create_procedural_midi(cfg: HVConfig,
                           filename: str,
                           coord_string: Optional[str] = None):
    cfg.seeds.finalize()
    if coord_string:
        parse_coord_to_parameters(coord_string, cfg)
    if cfg.bpm is None:
        cfg.bpm = random.Random(cfg.seeds.master_seed).randint(cfg.tempo_range[0], cfg.tempo_range[1])

    total_ticks = int(cfg.target_duration_seconds * (cfg.bpm / 60.0) * cfg.ppq)

    tracks = generate_tracks(cfg)
    sections = generate_sections_and_harmony(cfg, total_ticks, tracks)
    note_events = generate_all_events(cfg, tracks, sections)
    cc_curves = generate_cc_curves(cfg, tracks, total_ticks)
    pitch_curves = generate_pitch_bend_curves(cfg, tracks, total_ticks)
    tempo_map = build_tempo_map(cfg, total_ticks)

    mid = MidiFile(type=1)
    mid.ticks_per_beat = cfg.ppq

    track0 = MidiTrack()
    mid.tracks.append(track0)

    last_tick = 0
    for tick, bpm in tempo_map:
        delta = tick - last_tick
        if delta < 0: delta = 0
        last_tick += delta
        track0.append(MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=delta))

    track0.append(
        MetaMessage("time_signature", numerator=4, denominator=4, clocks_per_click=24, notated_32nd_notes_per_beat=8,
                    time=0))
    if coord_string:
        track0.append(MetaMessage("text", text=f"HVCOORD:{coord_string}", time=0))
    track0.append(MetaMessage("end_of_track", time=1))

    for t in tracks:
        track = MidiTrack()
        mid.tracks.append(track)

        track.append(Message("control_change", control=0, value=t.bank_msb, channel=t.channel, time=0))
        track.append(Message("control_change", control=32, value=t.bank_lsb, channel=t.channel, time=0))
        track.append(Message("program_change", program=t.program, channel=t.channel, time=0))

        events = []
        for abs_tick, note, vel in note_events[t.id]: events.append(('note', abs_tick, note, vel))
        for tick, msgs in cc_curves[t.id]:
            for msg in msgs: events.append(('cc', tick, msg))
        for tick, msg in pitch_curves[t.id]: events.append(('pb', tick, msg))

        events.sort(key=lambda e: e[1])
        last_tick = 0
        for item in events:
            kind = item[0]
            abs_tick = max(0, item[1])
            delta = max(0, abs_tick - last_tick)
            last_tick += delta

            if kind == 'note':
                _, _, note, vel = item
                msg_type = 'note_on' if vel > 0 else 'note_off'
                track.append(Message(msg_type, note=note, velocity=vel, time=delta, channel=t.channel))
            elif kind in ('cc', 'pb'):
                _, _, msg = item
                msg.time = delta
                track.append(msg)

        track.append(MetaMessage("end_of_track", time=0))

    mid.save(filename)
    print(f"\n[SUCCESS] HaloVision score saved to {filename}")

def convert_midi_to_audio(midi_path: str, output_path: str, format: str = "wav"):
    """
    Requires fluidsynth to be installed on the system, and a valid .sf2 SoundFont.
    """
    print(f"[DEBUG] Looking for SoundFont at: {SOUNDFONT_PATH}")

    if not os.path.exists(SOUNDFONT_PATH):
        print(f"[ERROR] SoundFont not found at '{SOUNDFONT_PATH}'.")
        return

    print(f"Synthesizing {format.upper()} using FluidSynth...")

    # Dynamically detect OS and set search paths
    system = platform.system()
    if system == "Windows":
        search_paths = [
            r"C:\fluidsynth\bin\fluidsynth.exe",
            r"C:\Program Files\fluidsynth\bin\fluidsynth.exe",
            "fluidsynth.exe",
            "fluidsynth"
        ]
    else: # Mac and Linux
        search_paths = [
            "/opt/homebrew/bin/fluidsynth",  # Mac Apple Silicon
            "/usr/local/bin/fluidsynth",     # Mac Intel
            "/usr/bin/fluidsynth",           # Standard Linux
            "fluidsynth"                     # System PATH
        ]

    fs_cmd = None

    # Test paths until we find a working executable
    for path in search_paths:
        try:
            subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            fs_cmd = path
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    if not fs_cmd:
        print(f"[ERROR] Could not locate 'fluidsynth' executable on this {system} system.")
        print("Please ensure FluidSynth is installed and added to your system PATH.")
        return

    try:
        # Force -T wav so it strictly renders a file instead of playing live audio
        cmd = [
            fs_cmd, "-ni",
            "-T", "wav",
            "-F", output_path,
            "-r", "44100",
            SOUNDFONT_PATH,
            midi_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            print(f"[ERROR] FluidSynth failed with output:\n{result.stderr}")
        else:
            print(f"[SUCCESS] Audio exported to {output_path}")

    except Exception as e:
        print(f"[ERROR] Synthesis crashed: {str(e)}")



# =====================================================================
# 5. API / GUI ENTRY POINTS
# =====================================================================

def build_coord(store: str, room: str, rack: str, crate: str, sleeve: str) -> str:
    return f"Store={store};Room={room};Rack={rack};Crate={crate};Sleeve={sleeve}"


def generate_from_hvcoord(hvcoord_seed: str, out_mid: str):
    coord = hvcoord_to_coord(hvcoord_seed)
    seeds = coord_to_seeds(coord)
    cfg = HVConfig(seeds=seeds, target_duration_seconds=90.0)
    create_procedural_midi(cfg, out_mid, coord_string=coord)


def process_gui_request(coord_string: str, export_audio: bool = False, audio_format: str = "wav", out_dir: str = "static") -> dict:
    """
    Main entry point for the GUI app backend.
    Takes a coordinate string, generates the MIDI, and optionally converts it to audio.
    """
    hvcoord_seed = coord_to_hvcoord(coord_string)
    h = hashlib.sha256(coord_string.encode("utf-8")).hexdigest()[:10]

    # Use the passed out_dir instead of hardcoding "static"
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    midi_file = os.path.join(out_dir, f"hv_{h}.mid")

    # 1. Generate MIDI
    generate_from_hvcoord(hvcoord_seed, midi_file)
    result = {"status": "success", "midi_path": midi_file, "seed": hvcoord_seed, "audio_path": None}

    # 2. Generate Audio if requested
    if export_audio and CAN_EXPORT_AUDIO:
        audio_file = os.path.join(out_dir, f"hv_{h}.{audio_format}")
        convert_midi_to_audio(midi_file, audio_file, audio_format)
        if os.path.exists(audio_file):
            result["audio_path"] = audio_file

    return result



# =====================================================================
# 6. CLI
# =====================================================================

def ask_for_audio_export(midi_path: str):
    ans = input("Would you like to also export this as a .wav audio file? (y/n): ").strip().lower()
    if ans == 'y':
        out_wav = midi_path.replace(".mid", ".wav")
        convert_midi_to_audio(midi_path, out_wav, "wav")


def main():
    print("==================================================")
    print("          HaloVision Crate Digging Engine         ")
    print("==================================================")
    print("1. Navigate Store → HVCOORD → MIDI")
    print("2. HVCOORD → MIDI (decode coord seed)")
    print("3. Encode MIDI file → HVMIDI (1:1)")
    print("4. Decode HVMIDI → MIDI (1:1)")
    print("5. Raw Store coord → HVCOORD → MIDI (advanced)")
    print("==================================================")

    choice = input("Choose option (1–5): ").strip()

    if choice == '1':
        print("\n--- HALOVISION STORE NAVIGATION ---")
        store = input("Store (any string, default 'HaloVision'): ").strip() or "HaloVision"
        room = input("Room (any string/number): ").strip() or "Main"
        rack = input("Rack (any string/number): ").strip() or "1"
        crate = input("Crate (any string/number): ").strip() or "1"
        sleeve = input("Sleeve (any string/number): ").strip() or "1"

        coord = build_coord(store, room, rack, crate, sleeve)
        hvcoord_seed = coord_to_hvcoord(coord)

        print("\nHVCOORD seed for this score:")
        print(hvcoord_seed[:120] + "...")

        h = hashlib.sha256(coord.encode("utf-8")).hexdigest()[:10]
        out_mid = input(f"Output MIDI filename (default hv_{h}.mid): ").strip() or f"hv_{h}.mid"

        generate_from_hvcoord(hvcoord_seed, out_mid)
        ask_for_audio_export(out_mid)

    elif choice == '2':
        print("\n--- HVCOORD → MIDI ---")
        seed_input = input("Enter HVCOORD-... string or path to .txt: ").strip()
        if seed_input.endswith(".txt") and os.path.exists(seed_input):
            with open(seed_input, "r") as f:
                seed_input = f.read().strip()

        if not seed_input.startswith("HVCOORD-"):
            print("Not an HVCOORD seed.")
            return

        out_mid = input("Output MIDI filename (default out.mid): ").strip() or "out.mid"
        generate_from_hvcoord(seed_input, out_mid)
        ask_for_audio_export(out_mid)

    elif choice == '3':
        print("\n--- ENCODE MIDI → HVMIDI ---")
        path = input("MIDI file to encode: ").strip()
        if not os.path.exists(path):
            print("File not found.")
            return

        seed = midi_to_hvmidi(path)
        out_txt = path.replace(".mid", "_hvmidi.txt")
        with open(out_txt, "w") as f:
            f.write(seed)
        print(f"\nHVMIDI seed saved to {out_txt}")

    elif choice == '4':
        print("\n--- HVMIDI → MIDI ---")
        seed_input = input("Enter HVMIDI-... string or path to .txt: ").strip()
        if seed_input.endswith(".txt") and os.path.exists(seed_input):
            with open(seed_input, "r") as f:
                seed_input = f.read().strip()

        if not seed_input.startswith("HVMIDI-"):
            print("Not an HVMIDI seed.")
            return

        out_mid = input("Output MIDI filename (default out.mid): ").strip() or "out.mid"
        hvmidi_to_midi(seed_input, out_mid)

    elif choice == '5':
        print("\n--- RAW STORE COORD → HVCOORD → MIDI ---")
        coord = input("Enter coord like 'Store=HaloVision;Room=LoFi;Rack=Beats;Crate=NightDrive;Sleeve=042': ").strip()

        if "Store=" not in coord:
            print("Coordinate must at least contain Store=..., Room=..., Rack=..., Crate=..., Sleeve=...")
            return

        hvcoord_seed = coord_to_hvcoord(coord)
        print("\nHVCOORD seed:")
        print(hvcoord_seed[:120] + "...")

        h = hashlib.sha256(coord.encode("utf-8")).hexdigest()[:10]
        out_mid = input(f"Output MIDI filename (default hv_{h}.mid): ").strip() or f"hv_{h}.mid"

        generate_from_hvcoord(hvcoord_seed, out_mid)
        ask_for_audio_export(out_mid)

    else:
        print("Any string can be turned into a HaloVision Store coordinate or a MIDI seed in this system.")


if __name__ == "__main__":
    main()
