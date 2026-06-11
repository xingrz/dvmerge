"""dvmerge — align, merge, and report overlapping DV tape captures, the hdvmerge way.

A worn DV tape often reads poorly: you capture it in several passes (stop, rewind a little, retry
whenever mosaic starts creeping in), ending up with several ``.dv`` files that overlap and each
carry some damage. ``dvmerge`` runs them through ``dvrescue``'s frame-level merge — which aligns
every capture by the tape's absolute track number and picks each frame's cleanest copy — then turns
the result into a **re-capture list**: the exact tape spots where no capture has a clean frame,
labelled with both the tape SMPTE timecode (to cue on the deck) and the camera's recording clock.

The division of labour: ``dvrescue`` owns the hard part (alignment, block-level picking, writing a
valid DV stream with its AUX metadata intact). dvmerge owns the workflow around it — discover the
captures, drive one merge, parse its per-frame CSV log, and render the report you read each round.

Usage mirrors hdvmerge::

    dvmerge CLIP-*.dv                 # analyse: merge, print the re-capture list
    dvmerge CLIP-*.dv -o merged.dv    # same, then keep merged.dv and write merged.dv.report.md

The loop: run it -> re-capture the listed spots -> drop the new files in -> run again. dvrescue
folds each new pass in by tape position, and the list shrinks until only physically unreadable
spots remain.
"""

__version__ = "0.1.0"

# PAL DV is 25 fps; this is the default tape frame rate. NTSC DV (29.97) can be selected with
# --fps on the CLI. Everything in dvmerge counts in whole tape frames and converts for display.
DEFAULT_FPS = 25.0
