# The SED design book

One design, two faces (light and dark), a small set of shared objects. This book says what each object is and what it
must do. It never gives a number: every size, gap, corner, duration and layer lives once in `tokens.css`, and every
colour lives once per face in `faces.css`. Object dress lives in `objects/<object>.css`; `theme.ts` carries the same
law into the Mantine components.

| You want | Go to |
|---|---|
| The rule for an object | Part 2 of this book |
| A number | `tokens.css` |
| A colour | `faces.css` |
| How an object looks | `objects/<object>.css` (Mantine components: `theme.ts`) |

## Part 1: the standing laws

1. **One design, two faces.** Light is the default; dark is one click away (the top bar) and remembered. Every surface
   reads correctly in both.
2. **A page never restyles a shared object.** Buttons, chips, fields, tables, tiles, cards, menus and pop-ups are
   dressed once, centrally, and look the same on every page.
3. **A page owns size and the position of its own objects, never their look.** An object that exists on one page only
   (the front screen's greeting, the shell's tabs) is dressed in that page's or the shell's stylesheet, through tokens.
4. **One value, one place.** A number typed in a page or object file is a defect; name the token, or add one.
5. **One corner, one curve.** Every box takes the one corner. Only a search bar and a button filled with the accent
   take the full curve.
6. **Every box sits slightly forward.** Tiles, cards, panels, buttons and icon boxes wear the emboss and rise on hover
   (a row in a list or table answers with a tint instead).
7. **Alive, with restraint.** A page arrives with its sections fading in one after another; a selected tile breathes
   three times and settles; a face change cross-fades. Nothing moves for a person who asked their system for reduced
   motion. Pages never write their own animations.
8. **No all-caps, ever.** Hierarchy comes from weight and colour. Text never escapes its box: it wraps or truncates
   with the whole text still reachable.

## Part 2: the objects

### Button (`objects/button.css`)
Three kinds and no fourth. **Quiet** for everything that is not the main action (the panel fill and a hairline).
**Primary** for the one main action on a screen (the accent fill, white writing, the full curve). **Danger** for
deleting (the danger colour). As tall as its words; it rises on hover, settles when pressed, fades when unavailable and
shows a ring for the keyboard. Never two primaries on one screen.

### Chip and label (`objects/chip.css`)
A word-sized box, never all-caps, as tall as its words: a faint fill in one of the meanings and a hairline. A chip
answers the pointer, a label does not; nothing else differs. Meanings: good (green), needs a look (amber), bad (red),
plain for everything else. Mantine colour names used by pages map onto these meanings in `theme.ts`.

### Panel and card (`objects/panel.css`)
A bordered surface holding other things: the panel fill, one hairline, the one corner, sitting forward.

### Tile and card face (`objects/tile.css`)
A box you press that stands for a thing. Hover rises and takes the accent edge; selected wears the accent ring and a
glow that breathes three times. Every tile in one collection is the same size. A deck of big tiles never leaves one
tile alone on a row (`components/Deck.tsx`). Big tiles carry line art (`app/LineArt.tsx`): a neutral outline with accent
detail lines that draw themselves in on hover, never a glyph. A **card** is a tile wearing its card face, for one thing
in a collection shown as cards: three bands, the head (name and chips), the story (its words) and the foot (figures
and links) pinned to the bottom.

### List (`components/CollectionView.tsx`)
A collection that can flip between a list (the table) and cards. The switch sits at the right end of the collection's
head row, counts what it heads, and is remembered per collection. Today: applications, Change Request projects and
recent report files.

### Read-out figure (`objects/figure.css`, `components/Figure.tsx`)
A number with its label: a small quiet label and the one figure size on tabular numerals, optionally in a meaning's
ink. Every KPI tile, count card and small stat uses it.

### Table (`objects/table.css`)
One skin: the panel fill, a row line, a faint line between columns, header cells in the quiet ink, the row tint on
hover, numbers right-aligned. No stripes.

### Field you type in (`objects/field.css`)
One hairline and the panel fill. Focus turns the border accent and nothing else; an error is a red edge, a faint red
fill and a line saying what to fix. The search bar is the one control with a stated height and takes the full curve.

### Tabs and filters (`objects/tabs.css`)
Tabs pick a view on one sliding track; the picked tab wears the accent.

### Menus, pop-ups and tooltips (`objects/floating.css`)
Anything floating: the panel fill, a hairline, the one corner and the deep shadow. A pop-up is centred and the page
behind it always dims. Layers come from the ladder in `tokens.css` (content, menu, dialog, toast, tooltip).

### Icon, status dot and loading (`objects/icon.css`)
A small icon standing alone sits in a compact box and answers with the accent. The delete icon is the only icon with
no box. A status dot is round, in one of four states (live, busy, down, off). Loading is a skeleton while a page fills
and a spinner in the accent inside the thing you pressed.

### The accent
One colour (`--sed-accent-rgb` in `faces.css`), in four roles only: selected (edge and glow), the one to press (solid
fill), hover (edge), and wholly accent (light fill and edge).

## Part 3: the shell (`app/shell.css`)

- **Top strip:** a plain band in the accent on synthetic data, red on the real profile, amber while the data class is
  unknown. The class in words is in the status dot's tooltip and read out to screen readers.
- **Sidebar:** square, full height and flush left. The logo, a thin divider and the brand title (kept on this machine,
  `sed branding`), the pages grouped by module, the one quiet action (upload an export), the items waiting for review
  with a search, and the person. It collapses to icons with the hide tile on its edge.
- **Top bar:** back to the previous screen, page tabs (open with "+", close with the cross), and on the right Home, the
  status dot, the light/dark switch and Help.
- **Front screen:** the greeting and headline with one accent word, the one search, a suggestion, the three steps
  (bring in data, review findings, build reports), a deck of big tiles, and the brand watermark (kept on this machine)
  in the free space under the tiles, sized to fit.

## Part 4: taste

- One bold move per surface; keep everything around it quiet.
- A divider divides something real; numbering appears only when order carries meaning.
- Name things by what the person sees and does. A control says exactly what happens; an error says what went wrong
  and how to fix it.

## Part 5: keeping this book

- A change replaces the section that owns the rule; it never adds a note beneath it.
- No number lives here.
- A new shared object is added here, with its own file under `objects/`, before any page uses it.
