# Power-debug, 2026-09-20 — geprüfte Befunde

Zwei Durchläufe über 27 Module: der lokale Worker (qwen) auf elf, ein
Sonnet-Subagent auf sechzehn. **117 Einträge**, davon **69 mit vollständig
belegten Zitaten**. Jeder hier aufgeführte Eintrag ist von Hand gegen die Datei
geprüft — bestätigt oder verworfen, mit Grund.

Kein Eintrag trägt einen Schweregrad. Ein Schweregrad ist eine Meinung, und die
Messung dieses Tages sagt, was Meinungen hier wert sind: ein als HIGH
eingestufter Vorschlag hätte den Kern-Schätzer zerstört, und `debug.md` stufte
als **LOW** ein, was gemessen **4,83° Scherung** war.

---

## Bestätigt

### 1. `pad` wird zurückgelesen und nie geschrieben

`prefs.py:8` nennt es ausdrücklich die eine Ausnahme:

> „``pad`` is the one deliberate exception to 'paths only': it is an output
> preference the user sets by hand and **expects to survive**"

Es steht in der Erlaubnisliste (`prefs.py:25`), `cli.py:460` führt es als
gemerkte Einstellung, und `gui.py:4679` liest es beim Start zurück:

```python
if self._remembered.get("pad"):
    s.pad = self._remembered["pad"]
```

**Kein einziger `prefs.save()`-Aufruf im Projekt übergibt je `pad=`.** Geprüft
über alle zehn Aufrufstellen in `cli.py` und `gui.py`, einschliesslich des
dynamischen `prefs.save(**{key: value})` in `_remember_model`, das nur
ComfyUI-Modellschlüssel führt.

Folge: eine Voreinstellung, die der Benutzer von Hand setzt, überlebt den
Programmstart nie — und der Code ist darauf vorbereitet, sie
wiederherzustellen.

### 2. Drei Layout-Helfer, die die Anwendung nie aufruft

`cross_is_perfect` (`layout.py:439`), `cross_rows` (`layout.py:449`) und
`preview_row_height` (`layout.py:172`) werden aus `src/pc/` **nirgends**
gerufen. `gui.py` benutzt aus `layout` nur `glyph_stroke`, `initial_window`,
`loupe`, `mark_line_width`, `tool_glyph`, `tool_key`. Die zwei Treffer in
`gui.py` sind die Tk-Gruppenbezeichnung `uniform="cross_rows"`, nicht die
Funktion.

Alle drei haben Tests (`test_layout.py:168–211`). Grüne Abdeckung über Code,
den die Anwendung nicht erreicht — dieselbe Form wie die 0 Byte grosse
`test_sam2seg.py`, die „besteht", weil nichts darin steht.

Gefunden **unter** einem schwachen Befund: gemeldet war ein Docstring-Streit,
`cross_is_perfect` schreibe den Rückfall fälschlich `preview_row_height` zu.
Die Begründung des Melders („superseded") ist falsch — die Funktion existiert
und wird getestet. Der Hinweis stimmte trotzdem.

### 3. `--remember` speichert mehr, als es ankündigt

`cli.py:192`:

> „store --birefnet-model, --mask-file, -o and --focal-35mm as defaults for
> future runs"

`cli.py:579` speichert zusätzlich `comfy_url` und `comfy_workflow`.

Das verletzt den Grundsatz, den `prefs.py` in seinem eigenen Kopf aufstellt:
*„a setting that silently persists between runs is a setting nobody can reason
about — the whole project turns on a batch being reproducible from its command
line."*

### 4. Zwei Vorgabewerte für `fill_max_share`

| | |
|---|---|
| `config.py:187` | `fill_max_share: float = 0.40` |
| `inpaint.py:673` | `cap = float(getattr(settings, "fill_max_share", 0.35))` |

Der Rückfallwert greift nur bei einem Aufrufer ohne dieses Feld — bei echten
`Settings` nie. Trotzdem zwei Zahlen für eine Entscheidung, und die zweite ist
die, die ein Ersatzobjekt bekommt.

### 5. `MIN_BOX_PX` ist wirkungslos

`sam2seg.py:39` definiert `MIN_BOX_PX = 8`. Der Name steht im ganzen Projekt
**genau einmal** — in dieser Zeile. `gui.py:4108` verdrahtet stattdessen 5.

### 6. `_strip_fractions` verlangt eine Einheit, die sein Aufrufer nicht liefert

Mein eigener Code von heute. Der Docstring sagt „Full-resolution pixels in,
fractions out"; `gui._remember_strip` übergibt Analyse-Pixel **und**
Analyse-Breite. Die Rechnung stimmt — Bruch = Wert/Breite, jedes
zusammenpassende Paar ergibt denselben Bruch —, der Docstring ist zu eng
formuliert und damit falsch.

### 7. `roi_x` — ein Name, zwei Einheiten

Kein gemeldeter Befund, sondern das, was beim Prüfen eines gemeldeten übrig
blieb. `Result.roi_x` führt seit heute **Brüche**, `ReviewSession.roi_x` führt
**Analyse-Pixel**. Die beiden treffen sich nirgends, es ist also kein aktiver
Fehler — aber zwei gleichnamige Felder mit verschiedenen Einheiten sind die
Falle, aus der heute schon zwei Fehler kamen.

---

## Verworfen (jeweils geprüft)

| Behauptung | Warum sie fällt |
|---|---|
| Die Checkbox „mask marks what to KEEP" widerspreche dem Loader, der Weiß als „ignore" behandelt (**drei Einträge**) | `masks.load`: *„White means 'ignore this region' **unless** ``invert``"*, `return ~m if invert else m`. Die Checkbox **ist** die invert-Flagge: angehakt bedeutet Weiß behalten. Der Melder hat die Vorgabe mit dem angehakten Zustand verwechselt |
| Die GUI skaliere die SAM-Maske auf volle Auflösung, während `apply_sam_mask` gegen die Analysegröße vergleiche | Der Docstring sagt selbst: *„``ignore`` may arrive at **either** resolution and is resized here"* |
| `Result.roi_x` (Bruch) und `session.roi_x` (Analyse-Pixel) würden in `would_skip` vermengt | `review.py` benutzt `self.roi_x` nur als Vorhandenseinsprüfung und als Analyse-Pixel; das `Result` erreicht diesen Pfad nie |
| `preview_row_height` sei überholt | Existiert, `layout.py:172`, mit vier Tests |
| Mehrere Einträge gegen meine Verlaufsnotizen von heute („the quad came back", „--roi-x once promised pixels") | Das Paket erlaubt ausdrücklich Vergangenheitsform für Historie. Fehlalarm |

---

## Was der Durchlauf über die Methode sagt

**Das Zitat-Format wirkt — auf Zitate.** Im alten Format erfand ein Bericht
`deps.default_gdino_dir()`, eine Funktion, die es nicht gibt. In diesem Format
sind 69 von 117 Zitaten maschinell belegt, und der Sonnet-Subagent traf 6 von 6.

**Es wirkt nicht auf Schlussfolgerungen.** Ein echtes Zitat mit falscher
Deutung besteht die Prüfung. Die Maskenpolarität ist der Beleg: drei Einträge,
alle Zitate korrekt, alle drei Deutungen falsch. Und sie kamen als **Bündel** —
was leicht wie Bestätigung aussieht und keine ist.

**Das gefährlichste Werkzeug war mein eigenes.** `verify_findings.py` liess
anfangs 8 von 52 Einträgen durch. Zwei Fehler steckten im Prüfer: maskierte
Anführungszeichen in zitiertem Code, dann umbrochene Kommentare, deren `#` in
der Quelle steht und im Zitat nicht. Nach beiden Korrekturen: 69. Das Werkzeug,
das entscheidet, was echt ist, hatte fünf von sechs echten Befunden verworfen —
und ein verworfener Befund sieht genauso aus wie einer, den es nie gab.

**Unvollständige Abschnitte, ehrlich gezählt:** 19 der 62 Ausschnitte wurden
abgeschnitten, allein neun in `gui.py`. Sie tragen den Vermerk im Kopf ihrer
Datei. Ein abgebrochener Durchlauf, der wie ein sauberer aussieht, ist der
Fehler, gegen den dieser Treiber gebaut wurde.

**Geprüfter Umfang:** die sieben bestätigten Einträge und die fünf verworfenen
Gruppen sind einzeln gegen den Code gehalten. Die übrigen der 69 belegten
Einträge sind nach Kernsatz gesichtet, nicht einzeln nachgerechnet.
