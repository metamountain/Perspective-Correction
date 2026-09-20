# Horizontal-Auto: mathematische Grundlage

Gemessen am 2026-09-20 an `tests/assets/Horizontal/`, Prüffall `Platte_1.jpg`,
linke Fassade per ROI.

---

## 1. Was heute passiert, und warum es schief geht

Die Korrektur setzt sich aus **drei getrennt geschätzten Winkeln** zusammen:

| Winkel | Quelle | Datei |
|---|---|---|
| roll, pitch | vertikaler Fluchtpunkt | `model.py:418` |
| yaw | *stärkster* horizontaler Fluchtpunkt | `model.py:435` |

```python
yaw = math.atan2(-wv[2], wv[0])
yaw = (yaw + math.pi / 2.0) % math.pi - math.pi / 2.0
```

Zwei Konstruktionsfehler stecken darin.

**a) „Stärkster" ist keine Entscheidung darüber, welche Fassade gemeint ist.**
Eine Eckansicht zeigt zwei zueinander orthogonale Gebäuderichtungen. Beide
erzeugen einen horizontalen Fluchtpunkt. Gewählt wird nach Linienanzahl:

```
Platte.jpg      << yaw -43.1° supp 0.67  |  yaw +48.8° supp 0.21     (91.9° auseinander)
Platte_1.jpg    << yaw -40.0° supp 0.46  |  yaw +45.6° supp 0.32     (85.6° auseinander)
csm_klassik.jpg << yaw +43.8° supp 0.91  |  yaw -42.8° supp 0.05     (86.6° auseinander)
```

Das sind nicht konkurrierende Schätzungen derselben Größe. Das sind die **zwei
Seiten desselben Hauses**, und die Abstimmung entscheidet, welche frontal wird.

Der gespeicherte Lauf `hpc_save/Platte_1.json` zeigt die Folge:

```json
"before": { "yaw_deg": -48.608, "support": 0.463 },
"after":  { "yaw_deg":  43.009, "support": 0.704 },
"correction": { "yaw_deg": -40.032 }
```

Vorher dominiert −48,6°, korrigiert wird um −40,0°, **nachher dominiert +43,0°**.
Die Korrektur bringt die Horizontale nicht auf null — sie dreht die eine Fläche
frontal und die andere dadurch in die Flucht, worauf diese die Mehrheit der
Linien stellt. Ein zweiter Durchlauf würde zurückdrehen.

**b) Drei getrennte Winkel sind nicht gezwungen, zueinander zu passen.**
roll/pitch kommen aus dem vertikalen Fluchtpunkt, yaw aus einem horizontalen,
`f` aus einer dritten Quelle (EXIF, Prior, Optimierung). Nichts in dieser Kette
erzwingt, dass die beiden Richtungen am Ende senkrecht aufeinander stehen.

Gemessen über acht Fassaden (Rechtwinkligkeit im Ergebnis, `probe_endtoend`):

```
50er.jpg                  0.75° schief
Aulendorf_Schloss         1.53°
Chateau.jpg               6.06°
Mariánské_Hory            7.95°   (yaw auf 60° gekappt, Bild unverändert)
Ocker Fassade             0.70°
Platte.jpg                2.84°
Platte_1.jpg              6.12°
Presserundgang           10.31°
```

---

## 2. Die richtige Grundlage: eine Ebene, zwei Fluchtpunkte

Eine Fassade ist eine **Ebene**. Für die Entzerrung einer Ebene braucht man
weder drei Euler-Winkel noch eine Abstimmung — man braucht die zwei Richtungen,
die in dieser Ebene aufeinander senkrecht stehen.

### 2.1 Orthogonalitätsbedingung (Bild des absoluten Kegelschnitts)

Für zwei Fluchtpunkte **v₁, v₂** orthogonaler Weltrichtungen gilt

$$v_1^\top\, \omega\, v_2 = 0, \qquad \omega = (KK^\top)^{-1}$$

Mit quadratischen Pixeln, ohne Scherung und mit Hauptpunkt **c** = (c_x, c_y)
wird daraus die geschlossene Form

$$(u_1-c_x)(u_2-c_x) + (v_1-c_y)(v_2-c_y) + f^2 = 0$$

$$\boxed{\,f^2 = -\,(v_1-c)\cdot(v_2-c)\,}$$

Die Funktion steht bereits im Repo: `geometry.focal_from_orthogonal`
(`geometry.py:252`), bisher nur zur Brennweitenschätzung benutzt
(`model.py:90`, `model.py:228`) — **nicht** zur Ausrichtung.

### 2.2 Die Rotation direkt aus beiden Fluchtpunkten

Rückprojektion der Fluchtpunkte auf Kamerarichtungen:

$$d_x = \frac{K^{-1}v_h}{\lVert K^{-1}v_h\rVert}, \qquad
  d_y = \frac{K^{-1}v_v}{\lVert K^{-1}v_v\rVert}$$

Messrauschen lässt die beiden nicht exakt senkrecht stehen. Gram-Schmidt
liefert den nächstgelegenen exakt orthonormalen Rahmen — mehr darf eine
verrauschte Messung nicht behaupten:

$$d_y \leftarrow \frac{d_y - (d_y\cdot d_x)\,d_x}{\lVert\cdot\rVert}, \qquad
  d_z = d_x \times d_y$$

$$R = \begin{bmatrix} d_x^\top \\ d_y^\top \\ d_z^\top \end{bmatrix}, \qquad
  H = K\,R\,K^{-1}$$

**Warum das die Antwort ist:** die Zeilen einer Rotationsmatrix *sind* die
Achsen des Zielsystems. Wer die Fassadenhorizontale in Zeile 1 und die
Fassadenvertikale in Zeile 2 schreibt, bekommt sie auf die Bild-x- und
-y-Achse — **per Konstruktion**, nicht als Ergebnis einer Optimierung. Beide
Fluchtpunkte gehen dabei exakt ins Unendliche:

```
vertikaler   Fluchtpunkt -> Unendlich bei 90.00°
horizontaler Fluchtpunkt -> Unendlich bei  0.00°
```

Es bleibt **K·R·K⁻¹**, also eine Homographie und eine echte Rotation: das
Seitenverhältnis bleibt erhalten. Das unterscheidet sie von der reinen
Ebenen-Rektifikation über `A = [d₁ d₂]⁻¹`, die die Winkel ebenfalls auf 90°
zwingt, dabei aber beide Achsen unabhängig skaliert und die Fassade streckt.

### 2.3 Warum ROI nicht Kosmetik ist, sondern Teil der Mathematik

Die Herleitung setzt voraus, dass **v_h und v_v zur selben Ebene gehören**. Bei
einer Eckansicht ist das nur erfüllt, wenn die horizontalen Linien auf eine
Fassade eingeschränkt werden. Genau das leistet der ROI-Streifen:

```
horizontale Fluchtpunkte, ganzes Bild:  supp 0.46, 0.32, 0.12   <- Abstimmung
horizontale Fluchtpunkte, nur ROI:      supp 0.57, 0.26, 0.08   <- eine Fassade
```

„1 Fassade" ist keine Einschränkung des Verfahrens. Es ist seine Voraussetzung.

---

## 3. Gemessenes Ergebnis

`Platte_1.jpg`, linke Fassade, ROI x = 0,02…0,46 (Gebäudekante bei x ≈ 660/1320),
gemessen an freigeschnittener Fassade im **Ergebnisbild der echten Pipeline**:

| | Horizontale | Vertikale | aus dem Winkel |
|---|---|---|---|
| vorher | −1,33° | +91,13° | **2,46°** |
| heutiges Verfahren (ohne ROI) | +3,67° | +88,84° | **4,83°** |
| **Fassadenrotation (mit ROI)** | **+0,07°** | **+90,14°** | **0,07°** |

327 horizontale und 123 vertikale Linien; 75 % davon innerhalb von 1,16° bzw.
2,15° um den Median.

Die Brennweite aus der Orthogonalitätsbedingung (1051 px) und die des Modells
(1022 px, `refined`) liegen 2,8 % auseinander — die Bedingung ist also keine
fremde Annahme, sondern bestätigt, was ohnehin geschätzt wurde.

### 3.1 Zwei Dinge mussten dafür noch fallen

**Die Pitch-Dämpfung bei grossem Yaw** (`pipeline.py:119`). Sie multipliziert
den Pitch bei |yaw| > 20° mit bis zu 0,3 — begründet damit, dass „pitch
computed from all verticals (both facades) is invalid for the target facade".
Das ist eine Notlösung für genau das Problem, das die Fassadenrotation löst:
dort *ist* der Pitch gegen die Horizontale dieser einen Fassade
orthogonalisiert. Die drei Winkel sind dann keine drei Regler mehr, sondern
die Zerlegung **einer** Rotation — einen davon zu dämpfen schert das Ergebnis
wieder aus dem Winkel. Gemessen: **13,63° schief mit Dämpfung, 0,07° ohne.**
Sie bleibt für den alten Pfad aktiv.

**Die Konfidenz-Buchhaltung — mit einer wichtigen Einschränkung.** Meine
Messung lief über `P.process(roi_x=…)`, also über `pipeline.analyse`, das die
Evidenz **vor** der Fluchtpunktsuche einschränkt. Dort fällt die Konfidenz
unter das Annahmetor und das Bild wird unkorrigiert übersprungen.

Das gilt **nicht** für die GUI. `ReviewSession.set_roi_x` filtert einen bereits
detektierten Pool — ein anderer Eingriffspunkt — und bewegt die Konfidenz kaum
(gemessen in `f9659fc` über 11 Eckansichten: 0,59→0,59, 0,54→0,54, 0,67→0,68,
0,40→0,44, grösster Fall 0,69→0,57; keine überschreitet das Tor). Derselbe
Regler, derselbe Name, zwei verschiedene Wirkungen je nach Ort — das ist dort
als eigener offener Punkt vermerkt.

Für die Entscheidung *überspringen oder nicht* ist der interaktive Pfad
ausserdem bereits versorgt: `review.would_skip` nimmt einen handgezogenen
Streifen vom Tor aus, aus demselben Grund, aus dem es Kontrolllinien ausnimmt —
wenig Evidenz abzulehnen ist richtig, wenn ein Detektor sie erzeugt hat, und
falsch, wenn ein Mensch sie gesetzt hat. Das Veto in `pipeline.process` steht
dagegen bewusst: das ist der unbeaufsichtigte Lauf.

| | conf | stability | focal |
|---|---|---|---|
| kein ROI, alt | 0,542 | 0,753 | 0,720 |
| ROI, alt | 0,415 | 0,607 | 0,720 |
| ROI, Fassadenrotation (vorher) | 0,332 | 0,582 | 0,600 |
| ROI, Fassadenrotation (jetzt) | **0,470** | 0,582 | 0,850 |

Eine Brennweite aus `f² = −(v₁−c)·(v₂−c)` fiel durch das Raster auf den
0,60-Wert für „unbekannte Quelle". Das ist die falsche **Einstufung** für eine
geschlossene geometrische Bedingung, unabhängig davon, was sie freischaltet;
sie steht jetzt bei den anderen geometrischen Quellen (0,85).

Wichtig ist, dass Fenster und Note zusammenpassen. Die erste Fassung nahm alles
innerhalb eines **Faktors 2** an und gab ihm dann 0,85 — sie behauptete also
gerade dann, die Brennweite sei gut bestimmt, wenn die beiden unabhängigen
Schätzungen einander um 80 % widersprechen. Eine Bedingung ist nur so lange
*bestätigende* Evidenz, wie sie nahe bei dem landet, was die vertikale Evidenz
für sich gefunden hat. Das Fenster ist deshalb ±25 %; darüber hinaus steht die
Voraussetzung selbst in Zweifel — die beiden Fluchtpunkte sind dann nicht
senkrecht —, der alte Wert bleibt stehen und `ortho_focal_rejected` sagt es.

## 4. Offene Punkte

- **Bildausschnitt.** Ein Yaw von 40° dehnt die Leinwand von 1320×742 auf
  6000×3764. Die Fassade ist korrekt, das Ergebnis braucht einen Zuschnitt.
  Das ist eine Rahmen-, keine Geometriefrage.
- **`max_horizontal_deg = 60`** kappt drei der sechzehn Testbilder. Eine
  gekappte Rotation ist wieder eine Scherung — sie richtet nichts aus.
  Mariánské Hory kommt deshalb unverändert heraus.
- **Pitch-Dämpfung bei |yaw| > 20°** (`pipeline.py:117`) wurde für die
  Winkel-Komposition gebaut. Beim Ebenenverfahren gibt es keinen yaw, auf den
  sie sich beziehen könnte.
- **`review._roi_layer`** rechnet `roi_x / self.w * gw`, `set_roi_x` speichert
  aber bereits Analyse-Koordinaten (`review.py:1059`). Bei `scale < 1` ist der
  Streifen dadurch zu schmal. Für `Platte_1` ist `scale = 1,0`, die Messungen
  oben sind davon unberührt.

---

## Quellen

- [Liebowitz & Zisserman, *Metric Rectification for Perspective Images of Planes*, CVPR 1998](https://www.cs.ucf.edu/courses/cap6938-02/refs/liebowitz98metric.pdf)
- [Liebowitz, *Camera Calibration and Reconstruction of Geometry from Images*, Oxford 2001](https://www.robots.ox.ac.uk/~vgg/publications/2001/Liebowitz01/liebowitz01.pdf)
- [Fast Projective Image Rectification for Planar Objects with Manhattan Structure, arXiv:1912.01892](https://arxiv.org/abs/1912.01892)
- [Camera calibration using two or three vanishing points, FedCSIS 2012](https://annals-csis.org/proceedings/2012/pliks/110.pdf)
- [Direct Camera Calibration from Vanishing Points via Polynomial Solvers, ICCVW 2025](https://openaccess.thecvf.com/content/ICCV2025W/CALIPOSE/papers/Kosaka_Direct_Camera_Calibration_from_Vanishing_Points_via_Polynomial_Solvers_ICCVW_2025_paper.pdf)
