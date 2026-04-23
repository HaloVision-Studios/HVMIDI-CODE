================================================================================

HALOVISION · HVMIDI Procedural Crate Digger Engine

================================================================================

Navigate a procedurally-generated 3D record store and generate unique MIDI tracks from your path. Dive through Stores, Rooms, Racks, Crates, and Sleeves — every route you take becomes a one-of-a-kind musical seed.

WHAT IS THIS?
HaloVision HVMIDI is a standalone desktop application that turns spatial navigation into music. You explore an infinite procedural city in 3D, and the path you take — your coordinate address — is used to generate a unique MIDI composition that can be exported as a .mid or .wav file.

No two paths produce the same track. Every location in the map is a seed.

FEATURES
3D Visual Navigation Explore an infinite procedurally-generated city map. Each level of the hierarchy (Store, Room, Rack, Crate, Sleeve) has its own visual style.

5-Level Coordinate System Every track is addressed as Store > Room > Rack > Crate > Sleeve, forming a reproducible seed you can save and share.

HVMIDI Encoding / Decoding Encode any .mid file into a compact HVMIDI text string, or reconstruct a full MIDI file from one at any time.

HVCOORD Seeds Paste a compact HVCOORD seed string to jump directly to any location and regenerate its track.

Text Coordinate Input Manually type a coordinate path to navigate to any specific address without using the 3D map.

AI Synthesis Mode Toggle AI mode in the search bar to describe a track in plain text. The AI translates your description into musical coordinates and generates a track directly — no map navigation needed. (Requires a local LM Studio instance running on your machine.)

MIDI & WAV Export Download your generated track as a .mid file or render it to .wav.

Dark / Light Mode Full theme switching with accent colour adjustments across the UI.

NAVIGATION MODES
Visual Navigation (3D) Launch the 3D map, hover over buildings/objects to inspect them, select one, and press DIVE to go deeper into the hierarchy.

Text Coordinate Nav Type a raw coordinate string in the format: Store[name]Room[name]Rack[name]Crate[name]Sleeve[name]

HVCOORD Decode Paste an HVCOORD-... seed string to decode it and generate its track.

HVMIDI to MIDI Paste an HVMIDI string to reconstruct the original .mid file.

Encode MIDI to HVMIDI Upload any .mid file to convert it into a portable HVMIDI text string.

AI MODE
Enable the AI toggle in the search bar at any navigation level. With AI mode on, your typed text is interpreted by a local language model and converted into a full musical coordinate + track — bypassing the map entirely.

Requirements:

LM Studio installed and running locally
A compatible model loaded in LM Studio
Server active on localhost before launching HaloVision
SYSTEM REQUIREMENTS
Windows 10 or later (64-bit)
No installation required — run HaloVision.exe directly
For AI Mode: LM Studio (free, available at lmstudio.ai)
CREDITS
Developed by Tanish Satpal Built with Three.js, GSAP, Python, FluidSynth, and mido.
