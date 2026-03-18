# Problem statement

You have a procedural MIDI generator that currently outputs *pure noise*. You want a stepwise program (an **Aligner**) that progressively transforms that noise into music humans like — but without erasing the procedural “weirdness.” The musician must be able to see the output after each alignment step and stop the process at any stage.

---

# Two approaches (plain, side-by-side)

## Approach 1 — Rules / Music-theory Aligner (“Waymo”)

* Build an algorithmic pipeline that knows musical rules and applies **incremental** transforms to the MIDI seed.
* Examples of steps: quantize timing, snap pitches to a scale, remove density collisions, align harmony, infer chord structure.
* Pros: preserves procedural uniqueness (you *shift* values rather than replace them), predictable, fast (CPU/Python math), interpretable, easy to show intermediate stages.
* Cons: handcrafted rules can miss high-level stylistic cues (lo-fi feel vs dubstep).

## Approach 2 — Data-driven AI Pattern Recognizer (“Tesla”)

* Train an AI to learn the semantic difference between raw procedural noise and many human MIDI examples; use it to convert noise toward a target style.
* Pros: can infer high-level style (genre, groove, instrumentation) and perform complex translation tasks that are hard to rule.
* Cons: tends to *collapse* towards the training distribution (loses unique procedural identity), heavy compute, intermediate steps are less interpretable.

---

# Recommendation (short)

* **Use Approach 1 as the primary Aligner** (for the stepwise interface).
* **Use Approach 2 as an analyst/translator** for your seed-finding / continuation plan (i.e., map a four-bar input + user intent to coordinates in the procedural space).
  This preserves novelty while giving you powerful style translation when you actually want it.

---

# Why this combo works (concise reasoning)

* Rules-based alignment *nudges* existing material into musical space (preserves combinatorial richness).
* AI mapping translates *semantic* user intent → procedural seed (so the generator produces material already biased toward the musician’s target style).
* Final flow: **AI → procedural seed → procedural generator → rules-based Aligner (stepwise UI)**.

---

# Practical pipeline (stepwise, with UI)

1. **Input**

   * Raw procedural MIDI (seed + parameters) or a short human MIDI clip (4 bars).
2. **(Optional) Analyst AI**

   * If user supplied human seed: extract semantic tags (key, mode, tempo, density, groove, instrumentation, vibe).
   * Translate tags → procedural coordinate/seed candidates.
3. **Generator**

   * Procedural engine produces continuation(s) from seed(s).
4. **Aligner (Rules) — show musician each step**

   * Level 0 — *Raw* (no changes).
   * Level 1 — **Rhythm snap**: quantize to chosen grid (e.g., 16th, triplets). Parameter: snap strength (0–1).
   * Level 2 — **Tonal snap**: push pitches to nearest note in target scale/mode. Parameter: scale, snap strength.
   * Level 3 — **Density & voice separation**: remove overlapping collisions, assign voices (bass/melody/harmony), voice allocation heuristics.
   * Level 4 — **Harmony alignment**: infer chords; force bass to chord roots or acceptable inversions.
   * Level 5 — **Groove & microtiming**: add controlled microtiming offsets, velocity humanization, swing/groove templates.
   * Level 6 — **Structure/arrangement smoothing**: create repeated motifs, phrase boundaries, simple chord progressions (optional).
5. **User control**

   * Slider per level, preview, undo/step back, and a parameter panel (snap strength, allowed dissonance, retain-ratio of original notes).

---

# Implementation notes (concise)

* **Language / environment:** Python recommended (Mido or pretty_midi for MIDI IO). Real-time preview via DAW/MIDI out or audio render.
* **Algorithms & modules:**

  * Quantization: nearest grid + partial interpolation for snap strength.
  * Scale snapping: map pitch → set of allowed pitch classes using nearest-distance heuristics.
  * Density filtering: greedy voice assignment or minimum-conflict selection (keep highest-velocity / earliest note).
  * Chord inference: rolling window histogram of pitch classes → root + quality (major/minor/diminished/7th).
  * Groove: microtiming templates (learned or hand-designed); velocities via percentile scaling.
* **AI components (Approach 2 uses):**

  * Feature extractor from MIDI: tempo, key probability, note density, on/off histograms, interval distributions, rhythmic n-grams, instrument program histograms.
  * Classifier/encoder: small transformer or CNN on piano-roll / learned embeddings to output semantic tags or an embedding vector.
  * Search/mapper: KNN or small MLP that maps embedding → procedural seed space coordinates (or invertible mapping if possible).

---

# Seed finding / continuation plan (your “upcoming plan”)

* Input a short MIDI fragment + optional user genre tag.
* Either:

  * **Brute force / dynamic programming** over procedural parameter space to find seeds whose generated output matches a similarity metric to the fragment; or
  * **AI mapper**: train a model to predict the procedural seed coordinates from features of the fragment (faster at runtime).
* Similarity metric: combination of tonal histogram distance, rhythm n-gram distance, tempo/beat alignment, instrument distribution, and perceptual features (e.g., salience of onsets).
* Use the AI approach for scalability; fallback to DP/brute force over a reduced search subspace if high precision is required.

---

# UX / controls to preserve “weirdness”

* **Retain-ratio** parameter — percentage of original MIDI notes that must remain untouched.
* **Snap strength** — continuous [0..1] blending between original and snapped value.
* **Randomness window** — allow small stochastic perturbations after snapping to keep combinatorial richness.
* **Per-voice controls** — let user pick which tracks to align (e.g., align drums only, leave synth pad untouched).

---

# Risks & failure modes

* **Model collapse** (AI over-regularizes to training set) — mitigated by always starting from procedural seed + retain controls.
* **Over-quantization (robotic feel)** — provide microtiming and partial snap to avoid this.
* **Style mismatch** — use analyst AI only to *suggest* seeds; keep human in loop for final judgement.

---

# Quick pseudocode (aligner core)

```python
# simplified step pipeline
midi = load_midi(file)
for level in requested_levels:
    if level == 1:
        midi = rhythm_snap(midi, grid=subdivision, strength=snap_strength)
    if level == 2:
        midi = tonal_snap(midi, scale=target_scale, strength=tonal_strength)
    if level == 3:
        midi = density_filter(midi, max_voices=3)
    if level == 4:
        chords = infer_chords(midi)
        midi = align_harmony(midi, chords)
    if level == 5:
        midi = apply_groove(midi, template=groove_template, humanize=humanize_amount)
save_preview(midi)
```

---

# Evaluation & measures (brief)

* **Preservation score** = fraction of original notes / pitch intervals kept.
* **Musicality score** = harmonic consonance metrics (pitch class correlations, tonal centroid distance).
* **Diversity / combinatorial capacity** = entropy of generator outputs under small seed perturbations.
* Combine automatic metrics with human A/B testing.

---

# Next steps (actionable)

1. Implement a minimal rules-based aligner with Levels 0–3 and preview UI.
2. Add retain-ratio + snap-strength controls for each level.
3. Collect a small set of labeled MIDI examples per target style (for later AI training).
4. Prototype an analyst AI that maps 4 bars → tag vector → seed candidates.
5. Integrate the AI mapping as an optional “Style Seed” that feeds the procedural generator, then pass the result through the aligner.

---

# One-line summary

Use a **rules-based, stepwise Aligner** to preserve procedural uniqueness and show intermediate states; use **AI** as an *analyst/mapper* when you want to automatically find seeds or translate a short human clip into procedural coordinates.
