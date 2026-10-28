"""Human-like timing model (all values in seconds)."""
import random
from typing import Optional

COMMON_EN = set("""the be to of and a in that have i it for not on with he as you do at this but his by from
they we say her she or an will my one all would there their what so up out if about who get which go me when make
can like time no just him know take people into year your good some could them see other than then now look only
come its over think also back after use two how our work first well way even new want because any these give day
most us is are was were been has had did said very more many much where here why too""".split())

COMMON_NO = set("""og i jeg det at en et den til er som på de med han av ikke der så var meg seg men ett har om vi min
mitt ha hadde hun nå over da ved fra du ut sin dem oss opp man kan hans hvor eller hva skal selv sjøl her alle vil bli
ble blitt kunne inn når være kom noen noe ville dette også bare mer dag hvis fikk måtte sa gjøre hvordan ingen for
etter mot under mellom uten siden fordi derfor""".split())

COMMON_BIGRAMS = set("""th he in er an en et de re on at es st nd or te ti ng ed to it ar al le se ve me ha ou nt
is of ne ea ri ro co ra ch ic el ge il ig sk ke or ng ko om""".split())

SENTENCE_END = ".!?"
CLAUSE_END = ",;:"


class HumanTiming:
    def __init__(self, wpm: float = 60.0, rng: Optional[random.Random] = None,
                 variation: float = 0.08):
        self.rng = rng or random.Random()
        wpm = max(1.0, float(wpm))
        self.wpm = wpm * (1.0 + self.rng.uniform(-variation, variation))
        self.base = 60.0 / (self.wpm * 5.0)     # seconds per character
        self.count = 0

    # -- helpers ---------------------------------------------------------
    def _jitter(self, sigma: float = 0.3) -> float:
        j = self.rng.lognormvariate(-sigma * sigma / 2.0, sigma)
        return min(max(j, 0.4), 3.0)

    def fatigue(self) -> float:
        return 1.0 + min(0.15, self.count / 3000.0 * 0.10)

    # -- public API ------------------------------------------------------
    def char_delay(self, ch: str, prev: str = "", word: str = "") -> float:
        """Delay before pressing `ch`, given the previous char and current word."""
        self.count += 1
        d = self.base * self._jitter()
        w = word.lower()
        if w and (w in COMMON_EN or w in COMMON_NO):
            d *= 0.65
        if prev and (prev + ch).lower() in COMMON_BIGRAMS:
            d *= 0.8
        if ch.isupper() or (not ch.isalnum() and not ch.isspace()):
            d *= 1.3
        elif ch.isdigit():
            d *= 1.3
        d *= self.fatigue()
        # pauses driven by the previous character
        if prev == " ":
            d += 0.8 * self.base * self._jitter(0.4)
            if self.rng.random() < 0.015:
                d += self.rng.uniform(0.5, 2.0)          # "thinking"
        elif prev in SENTENCE_END and prev:
            d += self.base * self.rng.uniform(2.5, 5.0)
        elif prev in CLAUSE_END and prev:
            d += self.base * self.rng.uniform(1.2, 2.2)
        elif prev == "\n":
            d += self.base * self.rng.uniform(4.0, 8.0)
        return max(0.01, d)

    def backspace_delay(self) -> float:
        return self.rng.uniform(0.06, 0.13)

    def arrow_delay(self) -> float:
        """Navigation key (arrow / word jump): like a rapidly tapped or held key,
        ~25-55 ms with an occasional slightly longer hiccup."""
        d = self.rng.uniform(0.025, 0.055)
        if self.rng.random() < 0.06:
            d += self.rng.uniform(0.02, 0.05)
        return d

    def typo_reaction(self) -> float:
        return 0.35 * self.rng.uniform(0.8, 1.3)

    def revision_think(self) -> float:
        return self.rng.uniform(3.5, 4.5)
