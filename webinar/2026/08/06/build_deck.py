"""Buduje deck webinarowy Monolynx jako .pptx (do konwersji na Google Slides)."""

import pathlib

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

HERE = pathlib.Path(__file__).parent

BG = RGBColor(0x03, 0x07, 0x12)
CARD = RGBColor(0x0C, 0x10, 0x17)
LINE = RGBColor(0x24, 0x2B, 0x3A)
WHITE = RGBColor(0xEE, 0xF1, 0xF7)
GRAY = RGBColor(0x88, 0x91, 0xA4)
DIM = RGBColor(0x5B, 0x64, 0x78)
MONOTXT = RGBColor(0xC6, 0xCD, 0xDB)

ORANGE = RGBColor(0xE9, 0x57, 0x1B)
AMBER = RGBColor(0xF5, 0x9E, 0x0B)
GREEN = RGBColor(0x34, 0xD3, 0x99)
SKY = RGBColor(0x38, 0xBD, 0xF8)
INDIGO = RGBColor(0x81, 0x8C, 0xF8)

FONT = "Inter"
MONO = "Roboto Mono"

W, H = Inches(13.333), Inches(7.5)

prs = Presentation()
prs.slide_width, prs.slide_height = W, H
BLANK = prs.slide_layouts[6]


def slide(notes=""):
    s = prs.slides.add_slide(BLANK)
    bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG
    bg.line.fill.background()
    bg.shadow.inherit = False
    if notes:
        s.notes_slide.notes_text_frame.text = notes
    return s


def text(s, x, y, w, h, runs, size=18, color=WHITE, bold=False, font=FONT,
         align=PP_ALIGN.LEFT, spacing=1.0, space_after=0):
    """runs: str albo lista akapitów; akapit = str lub (str, dict-overrides)."""
    box = s.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0

    items = [runs] if isinstance(runs, str) else runs
    for i, item in enumerate(items):
        body, over = item if isinstance(item, tuple) else (item, {})
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = over.get("align", align)
        p.line_spacing = over.get("spacing", spacing)
        p.space_after = Pt(over.get("space_after", space_after))
        r = p.add_run()
        r.text = body
        f = r.font
        f.name = over.get("font", font)
        f.size = Pt(over.get("size", size))
        f.bold = over.get("bold", bold)
        f.color.rgb = over.get("color", color)
    return box


def kicker(s, label, color):
    text(s, Inches(0.9), Inches(0.62), Inches(9), Inches(0.4), label,
         size=13, color=color, bold=True, font=MONO)


def heading(s, title, sub=None):
    text(s, Inches(0.9), Inches(1.05), Inches(11.5), Inches(1.1), title,
         size=40, color=WHITE, bold=True)
    if sub:
        text(s, Inches(0.9), Inches(1.95), Inches(11.0), Inches(0.6), sub,
             size=19, color=GRAY)


def bullets(s, items, y=2.65, color=WHITE, size=20, width=11.0, x=0.9):
    paras = [(f"•   {it}", {"space_after": 16}) for it in items]
    text(s, Inches(x), Inches(y), Inches(width), Inches(3.2), paras,
         size=size, color=color, spacing=1.15)


def codeblock(s, lines, y=5.15, accent=ORANGE, height=1.25, x=0.9, width=11.5):
    card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y),
                              Inches(width), Inches(height))
    card.fill.solid()
    card.fill.fore_color.rgb = CARD
    card.line.color.rgb = accent
    card.line.width = Pt(1.25)
    card.shadow.inherit = False
    card.adjustments[0] = 0.06

    paras = [(ln, {"space_after": 6}) for ln in lines]
    text(s, Inches(x + 0.42), Inches(y + 0.26), Inches(width - 0.8),
         Inches(height - 0.4), paras, size=20, color=MONOTXT, font=MONO,
         bold=True, spacing=1.15)


def picture(notes=""):
    s = slide(notes)
    return s


def image_slide(path, notes=""):
    s = picture(notes)
    s.shapes.add_picture(str(HERE / path), 0, 0, width=W, height=H)
    return s


# ══════════════════════════════════════════════════════════════════════
# 01 - TYTUL
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "POWITANIE - 2 min.\n\n"
    "Nie zaczynaj od narzędzia. Zacznij pytaniem na salę: ile wiedzy o Waszym "
    "projekcie żyje wyłącznie w głowie jednej osoby? Poczekaj na reakcje. "
    "To ustawia całą resztę webinaru.\n\n"
    "Poproś od razu, żeby otworzyli terminal i Claude Code - za trzy slajdy "
    "będziemy instalować na żywo."
)
s.shapes.add_picture(str(HERE / "logo-white.png"), Inches(0.9), Inches(2.05),
                     width=Inches(4.9))
text(s, Inches(0.9), Inches(3.35), Inches(11), Inches(1.0),
     "Od pomysłu do wiedzy", size=44, color=WHITE, bold=True)
text(s, Inches(0.95), Inches(4.25), Inches(11), Inches(0.6),
     "Cykl wytwarzania oprogramowania z agentami AI", size=21, color=GRAY)
bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.95), Inches(5.05),
                         Inches(2.2), Inches(0.06))
bar.fill.solid()
bar.fill.fore_color.rgb = ORANGE
bar.line.fill.background()
bar.shadow.inherit = False
text(s, Inches(0.95), Inches(6.35), Inches(8), Inches(0.4),
     "Webinar  ·  6 sierpnia 2026  ·  Piotr Krych", size=14, color=DIM, font=MONO)

# ══════════════════════════════════════════════════════════════════════
# 02 - AGENDA
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "1 min.\n\n"
    "Podkreśl: to warsztat, nie pokaz slajdów. Każdy wychodzi z DZIAŁAJĄCĄ "
    "instalacją i pierwszym ticketem.\n\n"
    "Zapowiedz umowę: przy krokach oznaczonych 'razem' zatrzymujemy się "
    "i czekamy na potwierdzenia na czacie."
)
kicker(s, "AGENDA", ORANGE)
heading(s, "Co dziś zrobimy razem")
bullets(s, [
    "Założysz konto i zainstalujesz Monolynx - u siebie, na żywo",
    "Zainicjujesz projekt: toolchain i wiki",
    "Stworzysz i zrecenzujesz pierwszy ticket",
    "Puścisz agentów do pracy",
    "Domkniesz pętlę - wiedza wróci do wiki",
], y=2.5)

# ══════════════════════════════════════════════════════════════════════
# 03 - PRZERYWNIK 1
# ══════════════════════════════════════════════════════════════════════
image_slide("slide-1.png",
            "PRZERYWNIK - 30 s.\n\n"
            "Pokaż mapę: pięć kroków, jedna pętla. Mów WYŁĄCZNIE o kroku 1 - "
            "reszta jest celowo wygaszona, nie zdradzaj jej.\n\n"
            "Zwróć uwagę na pasek na dole: warstwa wiedzy jest od początku, "
            "wszystko inne na niej stoi.")

# ══════════════════════════════════════════════════════════════════════
# 04 - KONTO
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "OKNO INSTALACYJNE - 3 min. RAZEM Z SALĄ.\n\n"
    "Nie idź dalej, dopóki nie zobaczysz potwierdzeń na czacie.\n\n"
    "Konto jest wymagane, bo plugin uwierzytelnia się przez OAuth - bez konta "
    "instalacja nie ma się do czego zalogować.\n\n"
    "RYZYKO: miej przygotowane zapasowe konto demo na wypadek problemu "
    "z rejestracją u kogoś na sali."
)
kicker(s, "KROK 1  ·  SETUP", ORANGE)
heading(s, "Najpierw konto", "Bez niego instalacja nie ma się do czego zalogować")
bullets(s, [
    "Rejestracja zajmuje minutę",
    "Plugin loguje się przez OAuth - nie przez token wklejany ręcznie",
    "Potrzebujesz dostępu do minimum jednego projektu",
], y=2.75)
codeblock(s, ["https://monolynx.com"], y=5.35, height=0.95)

# ══════════════════════════════════════════════════════════════════════
# 05 - INSTALACJA
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "NAJBARDZIEJ RYZYKOWNY MOMENT WEBINARU. 5 min. RAZEM.\n\n"
    "Rób to powoli, komenda po komendzie. Sam też wpisuj na współdzielonym "
    "ekranie, żeby mieli wzorzec.\n\n"
    "Po drugiej komendzie otworzy się przeglądarka z logowaniem do Monolynx. "
    "Uprzedź o tym ZANIM się otworzy, inaczej część osób się wystraszy.\n\n"
    "Jeśli ktoś ma starszą instalację: /plugin marketplace update monolynx"
)
kicker(s, "KROK 1  ·  SETUP", ORANGE)
heading(s, "Dwie komendy", "Marketplace, potem plugin")
codeblock(s, [
    "/plugin marketplace add piotrkrych2/monolynx",
    "/plugin install monolynx@monolynx",
], y=2.65, height=1.75)
text(s, Inches(0.9), Inches(4.75), Inches(11.2), Inches(1.4), [
    ("Przeglądarka poprosi o zalogowanie do Monolynx. To OAuth - token API "
     "nie jest potrzebny.", {"space_after": 12}),
    ("Dostajesz: 14 skilli, 7 agentów i połączenie MCP. W jednej instalacji.", {}),
], size=18, color=GRAY, spacing=1.2)

# ══════════════════════════════════════════════════════════════════════
# 06 - WERYFIKACJA
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "2 min. Punkt kontrolny.\n\n"
    "Poproś o reakcję na czacie: kciuk w górę = widzę przewodnik.\n\n"
    "Jeśli komenda nie istnieje - plugin się nie wczytał. Restart Claude Code "
    "rozwiązuje to w 90% przypadków.\n\n"
    "Dopiero gdy większość ma zielone, idź dalej. To ostatni moment, żeby "
    "kogokolwiek pozbierać."
)
kicker(s, "KROK 1  ·  SETUP", ORANGE)
heading(s, "Sprawdź, czy działa")
codeblock(s, ["/monolynx:help"], y=2.5, height=0.95)
bullets(s, [
    "Wypisze przewodnik po 14 skillach - instalacja się udała",
    "Komenda nie istnieje? Plugin się nie wczytał - zrestartuj Claude Code",
    "Ten sam przewodnik masz zawsze pod ręką, gdy nie wiesz co dalej",
], y=3.95)

# ══════════════════════════════════════════════════════════════════════
# 07 - TOOLCHAIN
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "3 min. Pokaż na swoim projekcie.\n\n"
    "Sedno: skille muszą wiedzieć, czym uruchomić lint i testy. Bez tej strony "
    "zapytają, czy kontynuować BEZ weryfikacji - a wtedy tracisz gate jakości.\n\n"
    "Uruchamiasz raz per projekt. Wracasz tylko gdy zmieni się stack."
)
kicker(s, "KROK 1  ·  SETUP", ORANGE)
heading(s, "Projekt musi się przedstawić", "Skille muszą wiedzieć, czym Cię sprawdzić")
bullets(s, [
    "Wykrywa komendy lint i testów (Makefile, Python, Node, Rust, Go, PHP…)",
    "Zapisuje je jako stronę wiki \"toolchain\"",
    "Kroki 4 i 5 czytają ją, zanim zamkną ticket",
    "Raz per projekt",
], y=2.75)
codeblock(s, ["/monolynx:project-toolchain"], y=5.35, height=0.95)

# ══════════════════════════════════════════════════════════════════════
# 08 - WIKI INIT
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "3 min.\n\n"
    "Tu jest teza całego webinaru w miniaturze: wiki nie jest dokumentacją "
    "pisaną po fakcie, tylko rosnącym artefaktem, z którego system czyta.\n\n"
    "Odwołanie do metody Karpathy'ego - jeśli sala jest techniczna, warto "
    "wspomnieć, bo dodaje wiarygodności.\n\n"
    "Powiedz wprost: bez tego kroku krok 5 nie ma dokąd wracać."
)
kicker(s, "KROK 1  ·  SETUP", ORANGE)
heading(s, "Wiki jako rosnący artefakt", "Nie dokumentacja pisana po fakcie")
bullets(s, [
    "Metoda LLM Wiki - wiki rośnie z każdym cyklem, zamiast być pisana na koniec",
    "Tworzy strony systemowe: wiki-index, wiki-log, wiki-schema",
    "Opcjonalne - ale bez tego krok 5 nie ma dokąd wracać",
], y=2.75)
codeblock(s, ["/monolynx:wiki-init"], y=5.35, height=0.95)

# ══════════════════════════════════════════════════════════════════════
# 09 - PRZERYWNIK 2
# ══════════════════════════════════════════════════════════════════════
image_slide("slide-2.png",
            "PRZERYWNIK - 20 s.\n\n"
            "Krok 1 zapalony, dochodzi krok 2. Fundament gotowy, zaczynamy pracę.")

# ══════════════════════════════════════════════════════════════════════
# 10 - TICKET CREATE
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "DEMO NA ŻYWO - 6 min.\n\n"
    "Weź zadanie zaproponowane przez salę, nie przygotowane wcześniej. "
    "Efekt jest dużo mocniejszy, a ryzyko małe.\n\n"
    "Pokaż wygenerowany opis i zatrzymaj się na kryteriach akceptacji - "
    "to je zobaczy potem krytyk w kroku 4.\n\n"
    "Zwróć uwagę: skill sam poszedł do wiki i grafu. Nikt mu nie wklejał kontekstu."
)
kicker(s, "KROK 2  ·  TICKET", AMBER)
heading(s, "Ticket, który wie o projekcie", "Kontekst zbierany automatycznie")
bullets(s, [
    "Czyta wiki, graf zależności i kod - zanim cokolwiek napisze",
    "Generuje cel, kontekst, zakres, kryteria akceptacji i zależności",
    "Trafia do sprintu albo backlogu",
], y=2.75, color=WHITE)
codeblock(s, ["/monolynx:ticket-create dodaj eksport raportu do CSV"],
          y=5.35, height=0.95, accent=AMBER)

# ══════════════════════════════════════════════════════════════════════
# 11 - PRZERYWNIK 3
# ══════════════════════════════════════════════════════════════════════
image_slide("slide-3.png", "PRZERYWNIK - 20 s. Dochodzi krok 3.")

# ══════════════════════════════════════════════════════════════════════
# 12 - TICKET REVIEW
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "4 min. Demo na tym samym tickecie.\n\n"
    "Kluczowy przekaz ekonomiczny: pętla review jest tania, zła implementacja "
    "jest droga. Powiedz to wprost, tym zdaniem.\n\n"
    "Jeśli raport wyjdzie od razu zielony - pokaż celowo zepsuty ticket, "
    "żeby zobaczyli, jak wygląda 'SPRZECZNE'."
)
kicker(s, "KROK 3  ·  REVIEW", GREEN)
heading(s, "Tania pętla przed drogą pracą")
bullets(s, [
    "Sprawdza formę i zgodność założeń z wiki oraz kodem",
    "Raport: OK / DO POPRAWY, ZGODNE / SPRZECZNE",
    "Powtarzaj review → poprawka → review, aż wszystko zielone",
], y=2.6)
text(s, Inches(0.9), Inches(4.5), Inches(11), Inches(0.6),
     "Dobrze zrecenzowany ticket = szybsza realizacja.",
     size=22, color=GREEN, bold=True)
codeblock(s, ["/monolynx:ticket-review PROJ-12"], y=5.5, height=0.95, accent=GREEN)

# ══════════════════════════════════════════════════════════════════════
# 13 - PRZERYWNIK 4
# ══════════════════════════════════════════════════════════════════════
image_slide("slide-4.png", "PRZERYWNIK - 20 s. Dochodzi krok 4 - sedno demo.")

# ══════════════════════════════════════════════════════════════════════
# 14 - WORK
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "GŁÓWNE DEMO - 10 min.\n\n"
    "Puść /monolynx:work na tickecie z kroku 2. To będzie się mieliło - "
    "wykorzystaj ten czas na omówienie tabeli i na pytania z czatu.\n\n"
    "Podkreśl dwie rzeczy, które odróżniają to od 'poproszę AI o kod':\n"
    "1. Agenci dostają KONKRETNE PLIKI - o wspólnych plikach idą sekwencyjnie.\n"
    "2. Czerwone testy BLOKUJĄ zamknięcie. To nie jest sugestia."
)
kicker(s, "KROK 4  ·  PRACA", SKY)
heading(s, "Zespół agentów, nie jeden model")

col = [
    ("/monolynx:work", {"size": 20, "font": MONO, "bold": True, "color": SKY,
                        "space_after": 14}),
    ("powyżej 3 SP", {"space_after": 8}),
    ("Researcher + N agentów + krytyk", {"space_after": 8}),
    ("research obowiązkowy", {"space_after": 8}),
    ("nowy moduł, zmiana cross-warstwowa", {}),
]
text(s, Inches(0.9), Inches(2.55), Inches(5.4), Inches(2.6), col,
     size=17, color=GRAY, spacing=1.15)

col2 = [
    ("/monolynx:work-simple", {"size": 20, "font": MONO, "bold": True,
                               "color": SKY, "space_after": 14}),
    ("do 3 SP", {"space_after": 8}),
    ("1 dev + krytyk", {"space_after": 8}),
    ("research opcjonalny", {"space_after": 8}),
    ("hotfix, cleanup, drobna weryfikacja", {}),
]
text(s, Inches(7.0), Inches(2.55), Inches(5.4), Inches(2.6), col2,
     size=17, color=GRAY, spacing=1.15)

sep = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6.55), Inches(2.55),
                         Inches(0.015), Inches(2.3))
sep.fill.solid()
sep.fill.fore_color.rgb = LINE
sep.line.fill.background()
sep.shadow.inherit = False

text(s, Inches(0.9), Inches(5.45), Inches(11.5), Inches(1.2), [
    ("Agenci dostają konkretne pliki. Krytyk ocenia według rubryki, próg 82.",
     {"space_after": 10}),
    ("Czerwone testy blokują zamknięcie ticketu. To gate, nie sugestia.", {}),
], size=18, color=WHITE, spacing=1.2)

# ══════════════════════════════════════════════════════════════════════
# 15 - ANATOMIA /monolynx:work
# ══════════════════════════════════════════════════════════════════════
image_slide("work-slide.png",
            "5 min. Slajd, na którym MÓWISZ - nie taki, który sala czyta. "
            "Z ostatniego rzędu bulletów nikt nie odczyta, więc prowadź wzrok "
            "palcem albo kursorem.\n\n"
            "Zatrzymaj się na trzech rzeczach, reszta to tło:\n\n"
            "1. BOX 'GDZIE SKILL SIĘ ZATRZYMUJE' - cztery twarde gate'y. To "
            "odpowiedź na obawę 'AI zacommituje mi bzdury do main'. Powiedz "
            "wprost: domyślnie skill wypisuje komendy, nie wykonuje ich.\n\n"
            "2. RUBRYKA KRYTYKA - konkretne liczby: -40 za czerwone testy, "
            "-30 za zapis do bazy bez db.commit(). Ocena jest mierzalna, "
            "nie uznaniowa. To przekonuje deweloperów.\n\n"
            "3. ROZŁĄCZNE PLIKI - agenci o wspólnych plikach idą sekwencyjnie, "
            "bo równoległy zapis kończy się cichym nadpisaniem. Najbardziej "
            "techniczny szczegół na slajdzie i zwykle ten, który kupuje salę.\n\n"
            "Zapowiedz, że diagram wyślesz po webinarze - wtedy nikt nie "
            "próbuje go przepisywać na żywo.")

# ══════════════════════════════════════════════════════════════════════
# 16 - PIPELINES
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "3 min. Pokaż zakładkę Pipelines w panelu, na żywo, w trakcie pracy agentów.\n\n"
    "To jest moment 'wow' dla menedżerów na sali - widzą, że praca AI przestaje "
    "być czarną skrzynką.\n\n"
    "Zaznacz: raportowanie jest best-effort. Awaria modułu Pipelines nie "
    "zatrzymuje pracy nad ticketem."
)
kicker(s, "KROK 4  ·  PRACA", SKY)
heading(s, "Widać, co robią agenci", "Obserwowalność wzorowana na GitLab CI/CD")
bullets(s, [
    "Hierarchia pipeline → step → job: research, coding, wrap-up",
    "Każdy agent ma swój job, a jego raport ląduje jako strona wiki",
    "Status i czas na żywo w panelu",
    "Best-effort: awaria raportowania nie blokuje pracy",
], y=2.75)

# ══════════════════════════════════════════════════════════════════════
# 17 - PRZERYWNIK 5
# ══════════════════════════════════════════════════════════════════════
image_slide("slide-5.png",
            "PRZERYWNIK - 40 s. TO JEST PUENTA WIZUALNA.\n\n"
            "Dopiero teraz pojawia się pętla zwrotna na dole. Do tej pory sala "
            "widziała linię produkcyjną - ta klatka zamyka ją w cykl.\n\n"
            "Zatrzymaj się tu na chwilę dłużej niż przy poprzednich przerywnikach.")

# ══════════════════════════════════════════════════════════════════════
# 18 - DOMKNIECIE
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "4 min.\n\n"
    "Pokaż stronę wiki PRZED i PO wiki-sync-merge. Różnica jest tu całym "
    "argumentem - bez niej to tylko opis procesu.\n\n"
    "Uwaga przy sprint-end: complete_sprint przenosi niedokończone tickety "
    "do backlogu, dlatego skill prosi o potwierdzenie. Nie klikaj w biegu."
)
kicker(s, "KROK 5  ·  DOMKNIĘCIE", INDIGO)
heading(s, "Wiedza wraca", "Kontekst nie zostaje w git logu")
bullets(s, [
    "Po merge do main: wiki-sync-merge integruje zmiany z wiki",
    "Na koniec sprintu: INGEST, audyt wiki, sprzątanie logów, zamknięcie",
    "Następny ticket startuje z bogatszym kontekstem niż poprzedni",
], y=2.75)
codeblock(s, ["/monolynx:wiki-sync-merge", "/monolynx:sprint-end"],
          y=5.0, height=1.55, accent=INDIGO)

# ══════════════════════════════════════════════════════════════════════
# 19 - PUENTA
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "1 min. Zwolnij. Nie czytaj slajdu - powiedz to własnymi słowami.\n\n"
    "Kontrast do wyartykułowania: w klasycznym flow kontekst wycieka do git "
    "loga i głowy jednego dewelopera. Tutaj wraca do systemu.\n\n"
    "Zamknij tym samym pytaniem, którym otwierałeś webinar."
)
text(s, Inches(0.9), Inches(2.5), Inches(11.5), Inches(2.0), [
    ("To nie jest zbiór narzędzi.", {"space_after": 6}),
    ("To zamknięta pętla wiedzy.", {"color": ORANGE}),
], size=46, color=WHITE, bold=True, spacing=1.15)
text(s, Inches(0.95), Inches(4.75), Inches(10.5), Inches(1.2),
     "Każdy cykl robi system mądrzejszym. Kontekst nie wycieka do git loga "
     "ani do głowy jednej osoby - wraca tam, skąd wezmą go następne zadania.",
     size=20, color=GRAY, spacing=1.3)

# ══════════════════════════════════════════════════════════════════════
# 20 - CO DALEJ
# ══════════════════════════════════════════════════════════════════════
s = slide(
    "ZAMKNIĘCIE - 2 min, potem Q&A.\n\n"
    "Zostaw ten slajd na ekranie na cały czas pytań - komendy muszą być "
    "widoczne, gdy ktoś dopiero teraz zabiera się za instalację.\n\n"
    "Jedno konkretne wezwanie: niech każdy do końca dnia przepuści JEDEN "
    "prawdziwy ticket przez pełny cykl. Bez tego webinar wyparuje do jutra."
)
kicker(s, "CO DALEJ", ORANGE)
heading(s, "Zacznij od jednego ticketu", "Dziś, na prawdziwym zadaniu")
codeblock(s, [
    "/plugin marketplace add piotrkrych2/monolynx",
    "/plugin install monolynx@monolynx",
    "/monolynx:help",
], y=2.6, height=2.15)
text(s, Inches(0.9), Inches(5.2), Inches(11), Inches(1.2), [
    ("monolynx.com", {"size": 24, "color": ORANGE, "bold": True,
                      "space_after": 10}),
    ("Pytania: piotrkrych@monolynx.com", {"size": 17, "color": GRAY}),
], size=18, color=GRAY)


out = HERE / "monolynx-webinar-2026-08-06.pptx"
prs.save(str(out))
print(f"{out.name}  ·  {len(prs.slides.__iter__.__self__._sldIdLst)} slajdów  "
      f"·  {out.stat().st_size / 1024:.0f} KB")
