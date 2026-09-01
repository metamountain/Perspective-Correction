# Test assets

Drop architectural photographs here and `tests/test_assets.py` starts checking
against them. The suite skips cleanly when the folder is empty, so the
repository does not have to carry binaries.

Naming conventions the tests understand:

| name | meaning |
|---|---|
| `something_upright.jpg` | already straight; asserted to be **left unchanged** |
| `something_skip.jpg` | detection should refuse it; asserted to be **SKIPPED** |
| `something_f27.jpg` | the 35 mm-equivalent focal length is **known** to be 27 mm |
| anything else | only checked for sanity and repeatability |

`_f<NN>` exists because the focal length usually cannot be recovered from the
file. `FocalLengthIn35mmFilm` is the only EXIF tag that gives it directly, and
none of the assets here carries it; deriving it from plain `FocalLength` needs a
crop factor, and the crop factor needs `FocalPlaneXResolution`, which every
resave and downscale strips. So where the answer *is* known -- from the camera
model and the lens -- it is recorded in the name, which no image editor can
remove.

Present:

| asset | camera | 35 mm equivalent | how it is known |
|---|---|---|---|
| `camden-gfx100s-16mm.jpg` | Fujifilm GFX100S | **16 mm** | `FocalLengthIn35mmFilm` in EXIF |
| `heilsbronn-d7000-27mm.jpg` | Nikon D7000 | **27 mm** | `FocalLengthIn35mmFilm` in EXIF |
| `marienrode-alte-scheune-nikond3200-27mm.jpg` | Nikon D3200 | **27 mm** | `FocalLengthIn35mmFilm` in EXIF |
| `haseldorf-inspektorat-scheune-nikond50-33mm.jpg` | Nikon D50 | **33 mm** | `FocalLengthIn35mmFilm` in EXIF |
| `ulica-ma-cahcowskiego-w-sosnowcu-ilce7m4-16mm.jpg` | Sony A7 IV | **16 mm** | `FocalLengthIn35mmFilm` in EXIF |
| `hospital-nikon-d60_f27.jpg` | Nikon D60 (APS-C, x1.5) | **27 mm** | the `_f27` suffix; EXIF has only `FocalLength` |

Add more with `python tools/fetch_commons_asset.py <commons url>`. It refuses any
file without `FocalLengthIn35mmFilm`, preserves the tag through the resize, and
prints the attribution row below.

These are the **only assets that carry the 35 mm tag**, so they are the only
real-photo coverage the EXIF focal-length path has. They report
`f=NNmm(exif)` rather than a guess, and the 16 mm one reaches confidence 0.91 --
the highest in the set.

Both are also **flat-on facades**, which is the case CLAUDE.md calls the known
weakness: one horizontal direction fixes a point on the horizon, not the line,
so the focal length is not determined by lines alone. With the tag present they
test the other half of that problem -- what the geometry does when `f` is not in
doubt.

`hospital-nikon-d60_f27.jpg` is also the only modern multi-storey building shot
at an angle -- the rest are barns -- and it carries a flagpole, a true vertical
belonging to no facade. It is the photograph that exposed the round-trip
harness's border artifact.

## Provenance and licences

Most of the Commons files are **CC BY-SA**, which is share-alike. They are test
fixtures rather than part of the MIT-licensed source, but the repository does
then contain share-alike content, and that is a deliberate choice to be aware
of. `haseldorf-…` is plain CC BY, which is the cleaner licence to prefer when
there is a choice.

| asset | source | author | licence |
|---|---|---|---|
| `camden-gfx100s-16mm.jpg` | [Commons](https://commons.wikimedia.org/wiki/File:The_Standard_London_Camden_Town_Hall_Annexe_facade_at_night_2025_dllu.jpg) | Daniel Lu (User:Dllu) | CC BY-SA 4.0 |
| `heilsbronn-d7000-27mm.jpg` | [Commons](https://commons.wikimedia.org/wiki/File:Heilsbronn,_Alte_Poststra%C3%9Fe,_Scheune_zum_Viehhof-001.jpg) | Tilman2007 | CC BY-SA 3.0 |
| `marienrode-alte-scheune-nikond3200-27mm.jpg` | [Commons](https://commons.wikimedia.org/wiki/File:Landschaftsschutzgebiet_Klosterlandschaft_Marienrode_-_Westlicher_Teil_-_Alte_Scheune_(2).JPG) | Ragnar1904 | CC BY-SA 4.0 |
| `haseldorf-inspektorat-scheune-nikond50-33mm.jpg` | [Commons](https://commons.wikimedia.org/wiki/File:Haseldorf_Inspektorat_Scheune.JPG) | Huhu Uet | **CC BY 3.0** |
| `ulica-ma-cahcowskiego-w-sosnowcu-ilce7m4-16mm.jpg` | [Commons](https://commons.wikimedia.org/wiki/File:Ulica_Ma%C5%82cahcowskiego_w_Sosnowcu.jpg) | Krzysztof Poplawski | **CC BY 4.0** |

Both were downscaled to a long edge of 1800 px with EXIF preserved; nothing else
was altered. Verify with `piexif.load(path)["Exif"][41989]` (FocalLengthIn35mmFilm).

### What the newest assets cover

| asset | the case | what it does |
|---|---|---|
| Sosnowiec street | **wide-angle street, 16 mm** | small, sensible correction |

Three others were fetched at the same time and **deliberately not kept**, which
is worth recording because each earned its place on the reject pile:

* **two interior ceilings.** A coffered ceiling has a clean bundle of parallel
  lines and a perfectly good vanishing point, so both were confidently corrected
  -- one at confidence 0.57 with the pitch pinned to the clamp, throwing away
  41 % of the frame. That was worth finding and is fixed at the source: a
  correction past `--max-pitch` is now refused rather than trimmed to fit. See
  CLAUDE.md, "Beyond the limit means refuse, not trim". Keeping the photographs
  as well would only test an interior case the tool does not claim.
* **a skyscraper shot straight up its facade**, which measures a **31.6 deg**
  round-trip error at confidence 0.00. The tool refuses it correctly, so it
  demonstrates nothing the gate does not already say, while dragging the mean
  round-trip error over the whole set from 0.70 to 3.36 deg.

The rule both cases point at: an asset whose distortion is so extreme that the
tool refuses it is not a test of the estimator, it is a test of the refusal --
and one such asset is enough.

Useful cases to collect: a flat-on facade, a corner view, a strong upward tilt,
a crooked horizon with no verticals, an interior, a photo with heavy foliage in
front of the building, and a web JPEG with the EXIF stripped.
