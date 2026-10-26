"""Gen2MC, OLMo 3's "multiple-choice version of 5 short-form generative tasks" (OLMo 3, 2512.13961, sec. 3.3.4): CoQA (Reddy et al. 2019, 1808.07042), DROP (Dua et al. 2019, 1903.00161), Jeopardy, Natural Questions (Kwiatkowski et al. 2019) and SQuAD (Rajpurkar et al. 2016, 1606.05250)."""

import re
from collections.abc import Callable
from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze

# Every gen2mc row names its gold choice by letter. OLMES looks the key up in its own list rather
# than the row's `label` column (`["A", "B", "C", "D", "E"].index` in each `*_mc.py`), so a key
# outside A to E raises there and here.
_LETTERS = ("A", "B", "C", "D", "E")


def coqa_doc(row: dict[str, Any]) -> MCDoc:
    """A CoQA row as a doc: the dataset's own rendered prompt (the story, the earlier turns and the final question), then each answer (`oe_eval/tasks/oe_eval_tasks/coqa_mc.py`)."""
    return MCDoc(
        id=row["id"],
        choices=tuple(Choice(row["query_original"], f" {text}") for text in row["choices"]["text"]),
        gold=_LETTERS.index(row["answerKey"]),
    )


def drop_doc(row: dict[str, Any]) -> MCDoc:
    """A DROP row as a cloze doc after a `Passage: ` line (`oe_eval/tasks/oe_eval_tasks/drop_mc.py`)."""
    passage = row["passage_original"].strip()

    return MCDoc(
        id=row["id"],
        choices=cloze(
            row["question_original"],
            row["choices"]["text"],
            question_prefix=f"Passage: {passage}\nQuestion: ",
        ),
        gold=_LETTERS.index(row["answerKey"]),
    )


def jeopardy_doc(row: dict[str, Any]) -> MCDoc:
    """A Jeopardy row as a cloze doc after a `Category: ` line (`oe_eval/tasks/oe_eval_tasks/jeopardy_mc.py`)."""
    # OLMES's own split of `CATEGORY: clue`, kept as is: the first colon ends the category, a
    # newline ends the clue, and a row without a colon raises, as there.
    category, clue = re.findall(r"(.*?):\s*(.*)", row["context_original"])[0]

    return MCDoc(
        id=row["id"],
        choices=cloze(
            clue,
            row["choices"]["text"],
            question_prefix=f"Category: {category}\nQuestion: ",
        ),
        gold=_LETTERS.index(row["answerKey"]),
    )


def naturalqs_doc(row: dict[str, Any]) -> MCDoc:
    """A Natural Questions row as a cloze doc, on the rewritten question rather than the original lowercase query (`oe_eval/tasks/oe_eval_tasks/naturalqs_open_mc.py`)."""
    return MCDoc(
        id=row["id"],
        choices=cloze(row["question"], row["choices"]["text"]),
        gold=_LETTERS.index(row["answerKey"]),
    )


def squad_doc(row: dict[str, Any]) -> MCDoc:
    """A SQuAD row as a cloze doc after `Title: ` and `Passage: ` lines (`oe_eval/tasks/oe_eval_tasks/squad_mc.py`)."""
    title = row["title_original"]
    passage = row["context_original"].strip()

    return MCDoc(
        id=row["id"],
        choices=cloze(
            row["question_original"],
            row["choices"]["text"],
            question_prefix=f"Title: {title}\nPassage: {passage}\nQuestion: ",
        ),
        gold=_LETTERS.index(row["answerKey"]),
    )


_DROP_SHOTS = (
    {
        "id": "drop_mc_format_fewshot_0",
        "passage_original": (
            "To start the season, the Lions traveled south to Tampa, Florida to take on the Tampa "
            "Bay Buccaneers. The Lions scored first in the first quarter with a 23-yard field "
            "goal by Jason Hanson. The Buccaneers tied it up with a 38-yard field goal by Connor "
            "Barth, then took the lead when Aqib Talib intercepted a pass from Matthew Stafford "
            "and ran it in 28 yards. The Lions responded with a 28-yard field goal. In the second "
            "quarter, Detroit took the lead with a 36-yard touchdown catch by Calvin Johnson, and "
            "later added more points when Tony Scheffler caught an 11-yard TD pass. Tampa Bay "
            "responded with a 31-yard field goal just before halftime. The second half was "
            "relatively quiet, with each team only scoring one touchdown. First, Detroit's Calvin "
            "Johnson caught a 1-yard pass in the third quarter. The game's final points came when "
            "Mike Williams of Tampa Bay caught a 5-yard pass.  The Lions won their regular season "
            "opener for the first time since 2007"
        ),
        "question_original": "How many field goals did the Lions score?",
        "choices": {"text": ["1", "2", "3", "4"]},
        "answerKey": "B",
    },
    {
        "id": "drop_mc_format_fewshot_1",
        "passage_original": (
            "Coming off their road win over the Redskins, the Chiefs went home, donned their "
            "Dallas Texans throwbacks, and played a Week 7 AFL Legacy game with the San Diego "
            "Chargers. Kansas City would find themselves trailing in the first quarter as "
            "Chargers quarterback Philip Rivers completed a 3-yard touchdown pass to wide "
            "receiver Malcom Floyd, followed by a 10-yard touchdown pass to wide receiver Vincent "
            "Jackson. San Diego would add onto their lead in the second quarter with a 20-yard "
            "and a 39-yard field goal from kicker Nate Kaeding. The Chiefs would get onto the "
            "board in the third quarter with quarterback Matt Cassel completing a 7-yard "
            "touchdown pass to wide receiver Dwayne Bowe, but the Chargers kept their momentum "
            "going with Rivers finding running back Darren Sproles on a 58-yard touchdown pass. "
            "In the fourth quarter, San Diego sealed the win with Kaeding's 19-yard field goal "
            "and fullback Jacob Hester recovering a blocked punt in the end zone for a touchdown. "
            "With the loss, Kansas City went into their bye week at 1-6. Larry Johnson was "
            "suspended for two weeks after he made offensive comments about Todd Haley and made "
            "offensive comments about homosexuals on Twitter and in public."
        ),
        "question_original": "which player scored the longest field goal?",
        "choices": {"text": ["Nate Kaeding", "Dwayne Bowe", "Jacob Hester", "Philip Rivers"]},
        "answerKey": "A",
    },
    {
        "id": "drop_mc_format_fewshot_2",
        "passage_original": (
            "In 1344, Momchil, the independent Bulgarian ruler of the Rhodope and Aegean regions, "
            "whose army grew to 2,000 men, took an important role in the Byzantine civil war. "
            "While at first he supported John Kantakouzenos, from the spring of 1344 Momchil "
            "reneged, provoked by the aggression of the Ottoman allies. In June he defeated the "
            "Ottoman fleet near the Portogalos bay. According to sources, at night the Bulgarian "
            "ruler sent boats to burn the anchored Ottoman ships and soon after he defeated the "
            "army of Kantakouzenos at Mosynopolis. Probably the first local ruler to become aware "
            "of the impending Ottoman threat, Momchil unsuccessfully pleaded with the emperors of "
            "Bulgaria and Byzantium for help. Even though his troops continued the resistance in "
            "the Eastern Rhodopes, in May 1345 the Turks led by Umur Beg marched from Asia Minor "
            "and devastated Bulgarian territories driving away people and livestock. Soon after, "
            "on 7 July 1345, Ottoman forces under Umur Beg defeated Momchil's army in the battle "
            "of Peritor near his capital Xanthi. Sources attest that the independent ruler "
            "perished in the battle without leaving asuccessor, and with little political will or "
            "leadership left to counter the Ottoman invasion."
        ),
        "question_original": "When did Momchil defeat the Ottoman fleet near the Portogalos bay?",
        "choices": {"text": ["May 1345", "July 1345", "April 1344", "June 1344"]},
        "answerKey": "D",
    },
    {
        "id": "drop_mc_format_fewshot_3",
        "passage_original": (
            "The French king, John II, had been held captive in England. The Treaty of Brétigny "
            "set his ransom at 3 million crowns and allowed for hostages to be held in lieu of "
            "John. The hostages included two of his sons, several princes and nobles, four "
            "inhabitants of Paris, and two citizens from each of the nineteen principal towns of "
            "France. While these hostages were held, John returned to France to try and raise "
            "funds to pay the ransom. In 1362 John's son Louis of Anjou, a hostage in "
            "English-held Calais, escaped captivity. So, with his stand-in hostage gone, John "
            "felt honor-bound to return to captivity in England. The French crown had been at "
            "odds with Navarre  since 1354, and in 1363 the Navarrese used the captivity of John "
            "II in London and the political weakness of the Dauphin to try to seize power. "
            "Although there was no formal treaty, Edward III supported the Navarrese moves, "
            "particularly as there was a prospect that he might gain control over the northern "
            "and western provinces as a consequence. With this in mind, Edward deliberately "
            "slowed the peace negotiations. In 1364, John II died in London, while still in "
            "honourable captivity. Charles V succeeded him as king of France. On 7 May 1364, one "
            "month after the dauphin's accession and three days before his coronation as Charles "
            "V, the Navarrese suffered a crushing defeat at the Battle of Cocherel."
        ),
        "question_original": "What did one of John II's replacements do in captivity?",
        "choices": {
            "text": [
                "Led a rebellion",
                "Became king of France",
                "Escaped captivity",
                "Negotiated a peace treaty",
            ]
        },
        "answerKey": "C",
    },
    {
        "id": "drop_mc_format_fewshot_6",
        "passage_original": (
            "Hoping to rebound from their tough overtime road loss to the Raiders, the Jets went "
            "home for a Week 8 duel with the Kansas City Chiefs.  In the first quarter, New York "
            "took flight as QB Brett Favre completed an 18-yard TD pass to RB Leon Washington.  "
            "In the second quarter, the Chiefs tied the game as QB Tyler Thigpen completed a "
            "19-yard TD pass to TE Tony Gonzalez.  The Jets would answer with Washington getting "
            "a 60-yard TD run.  Kansas City closed out the half as Thigpen completed an 11-yard "
            "TD pass to WR Mark Bradley. In the third quarter, the Chiefs took the lead as kicker "
            "Connor Barth nailed a 30-yard field goal, yet New York replied with RB Thomas Jones "
            "getting a 1-yard TD run.  In the fourth quarter, Kansas City got the lead again as "
            "CB Brandon Flowers returned an interception 91 yards for a touchdown.  Fortunately, "
            "the Jets pulled out the win with Favre completing the game-winning 15-yard TD pass "
            "to WR Laveranues Coles. During halftime, the Jets celebrated the 40th anniversary of "
            "their Super Bowl III championship team."
        ),
        "question_original": "How many yards was the first TD pass?",
        "choices": {"text": ["30", "12", "25", "18"]},
        "answerKey": "D",
    },
)

_JEOPARDY_SHOTS = (
    {
        "id": "jeopardy_mc_format_fewshot_0",
        "context_original": (
            "HISTORY: Under the 1814 Treaty of Kiel, this country gave Norway to Sweden but kept "
            "Greenland & other islands"
        ),
        "choices": {"text": ["Iceland", "Denmark", "Finland", "Netherlands"]},
        "answerKey": "B",
    },
    {
        "id": "jeopardy_mc_format_fewshot_1",
        "context_original": (
            "U.S. HISTORY: In the 1968 election, he won 13 1/2 percent of the popular vote & "
            "carried 5 southern states"
        ),
        "choices": {
            "text": ["George Wallace", "Barry Goldwater", "Strom Thurmond", "Hubert Humphrey"]
        },
        "answerKey": "A",
    },
    {
        "id": "jeopardy_mc_format_fewshot_2",
        "context_original": (
            'SHAKESPEARE: In "The Merchant of Venice" he tells his friend Tubal, "Meet me at '
            'our synagogue"'
        ),
        "choices": {"text": ["Antonio", "Bassanio", "Gratiano", "Shylock"]},
        "answerKey": "D",
    },
    {
        "id": "jeopardy_mc_format_fewshot_3",
        "context_original": (
            "SCIENCE & NATURE: Sir Humphry Davy named this yellowish-green gas from a Greek word "
            'meaning "greenish-yellow"'
        ),
        "choices": {"text": ["Fluorine", "Sulfur", "Chlorine", "Radon"]},
        "answerKey": "C",
    },
    {
        "id": "jeopardy_mc_format_fewshot_5",
        "context_original": (
            "HISTORY: In the midst of the Korean War, this South Korean president was elected to "
            "his second of 4 terms"
        ),
        "choices": {"text": ["Syngman Rhee", "Kim Il-sung", "Moon Jae-in", "Park Chung-hee"]},
        "answerKey": "A",
    },
)

_NATURALQS_SHOTS = (
    {
        "id": "nq_open_mc_format_fewshot_0",
        "question": "Which side of the White House is the front?",
        "choices": {"text": ["East", "North", "South", "West"]},
        "answerKey": "B",
    },
    {
        "id": "nq_open_mc_format_fewshot_1",
        "question": "Where was the Super Bowl hosted in 2019?",
        "choices": {
            "text": [
                "Atlanta, Georgia",
                "Houston, Texas",
                "Los Angeles, California",
                "Miami, Florida",
            ]
        },
        "answerKey": "A",
    },
    {
        "id": "nq_open_mc_format_fewshot_2",
        "question": "What is the origin of the name Cynthia?",
        "choices": {"text": ["Latin", "Hebrew", "Norse", "Greek"]},
        "answerKey": "D",
    },
    {
        "id": "nq_open_mc_format_fewshot_7",
        "question": "Who composed and performed the theme song for Miami Vice?",
        "choices": {"text": ["Danny Elfman", "Hans Zimmer", "Jan Hammer", "John Williams"]},
        "answerKey": "C",
    },
    {
        "id": "nq_open_mc_format_fewshot_4",
        "question": "What is the size of the angles of an equilateral triangle?",
        "choices": {"text": ["90°", "60°", "120°", "45°"]},
        "answerKey": "B",
    },
)

_SQUAD_SHOTS = (
    {
        "id": "squad_mc_format_fewshot_0",
        "title_original": "University_of_Notre_Dame",
        "context_original": (
            "Architecturally, the school has a Catholic character. Atop the Main Building's gold "
            "dome is a golden statue of the Virgin Mary. Immediately in front of the Main "
            "Building and facing it, is a copper statue of Christ with arms upraised with the "
            'legend "Venite Ad Me Omnes". Next to the Main Building is the Basilica of the '
            "Sacred Heart. Immediately behind the basilica is the Grotto, a Marian place of "
            "prayer and reflection. It is a replica of the grotto at Lourdes, France where the "
            "Virgin Mary reputedly appeared to Saint Bernadette Soubirous in 1858. At the end of "
            "the main drive (and in a direct line that connects through 3 statues and the Gold "
            "Dome), is a simple, modern stone statue of Mary."
        ),
        "question_original": (
            "To whom did the Virgin Mary allegedly appear in 1858 in Lourdes France?"
        ),
        "choices": {
            "text": [
                "Saint Thomas Aquinas",
                "Saint Bernadette Soubirous",
                "Saint Francis of Assisi",
                "Saint Joan of Arc",
            ]
        },
        "answerKey": "B",
    },
    {
        "id": "squad_mc_format_fewshot_1",
        "title_original": "Beyoncé",
        "context_original": (
            "Beyoncé Giselle Knowles-Carter (/biːˈjɒnseɪ/ bee-YON-say) (born September 4, 1981) "
            "is an American singer, songwriter, record producer and actress. Born and raised in "
            "Houston, Texas, she performed in various singing and dancing competitions as a "
            "child, and rose to fame in the late 1990s as lead singer of R&B girl-group Destiny's "
            "Child. Managed by her father, Mathew Knowles, the group became one of the world's "
            "best-selling girl groups of all time. Their hiatus saw the release of Beyoncé's "
            "debut album, Dangerously in Love (2003), which established her as a solo artist "
            "worldwide, earned five Grammy Awards and featured the Billboard Hot 100 number-one "
            'singles "Crazy in Love" and "Baby Boy".'
        ),
        "question_original": "When did Beyonce leave Destiny's Child and become a solo singer?",
        "choices": {"text": ["2003", "1995", "1999", "2010"]},
        "answerKey": "A",
    },
    {
        "id": "squad_mc_format_fewshot_2",
        "title_original": "Montana",
        "context_original": (
            "Montana i/mɒnˈtænə/ is a state in the Western region of the United States. The "
            "state's name is derived from the Spanish word montaña (mountain). Montana has "
            'several nicknames, although none official, including "Big Sky Country" and "The '
            'Treasure State", and slogans that include "Land of the Shining Mountains" and '
            'more recently "The Last Best Place". Montana is ranked 4th in size, but 44th in '
            "population and 48th in population density of the 50 United States. The western third "
            "of Montana contains numerous mountain ranges. Smaller island ranges are found "
            "throughout the state. In total, 77 named ranges are part of the Rocky Mountains."
        ),
        "question_original": "What is the states rank in size?",
        "choices": {"text": ["10th", "25th", "50th", "4th"]},
        "answerKey": "D",
    },
    {
        "id": "squad_mc_format_fewshot_3",
        "title_original": "Genocide",
        "context_original": (
            "In the same judgement the ECHR reviewed the judgements of several international and "
            "municipal courts judgements. It noted that International Criminal Tribunal for the "
            "Former Yugoslavia and the International Court of Justice had agreed with the narrow "
            "interpretation, that biological-physical destruction was necessary for an act to "
            "qualify as genocide. The ECHR also noted that at the time of its judgement, apart "
            "from courts in Germany which had taken a broad view, that there had been few cases "
            'of genocide under other Convention States municipal laws and that "There are no '
            "reported cases in which the courts of these States have defined the type of group "
            "destruction the perpetrator must have intended in order to be found guilty of "
            'genocide".'
        ),
        "question_original": (
            "Two bodies of the United Nations agreed with what restricted provision in defining "
            "genocide?"
        ),
        "choices": {
            "text": [
                "That political destruction was sufficient",
                "That economic destruction was necessary",
                "That biological-physical destruction was necessary",
                "That cultural destruction was sufficient",
            ]
        },
        "answerKey": "C",
    },
    {
        "id": "squad_mc_format_fewshot_4",
        "title_original": "Antibiotics",
        "context_original": (
            "The emergence of resistance of bacteria to antibiotics is a common phenomenon. "
            "Emergence of resistance often reflects evolutionary processes that take place during "
            "antibiotic therapy. The antibiotic treatment may select for bacterial strains with "
            "physiologically or genetically enhanced capacity to survive high doses of "
            "antibiotics. Under certain conditions, it may result in preferential growth of "
            "resistant bacteria, while growth of susceptible bacteria is inhibited by the drug. "
            "For example, antibacterial selection for strains having previously acquired "
            "antibacterial-resistance genes was demonstrated in 1943 by the Luria–Delbrück "
            "experiment. Antibiotics such as penicillin and erythromycin, which used to have a "
            "high efficacy against many bacterial species and strains, have become less "
            "effective, due to the increased resistance of many bacterial strains."
        ),
        "question_original": (
            "Which two antibiotics that have high efficacy are much less useful now?"
        ),
        "choices": {
            "text": [
                "Ciprofloxacin and vancomycin",
                "Penicillin and erythromycin",
                "Azithromycin and doxycycline",
                "Amoxicillin and tetracycline",
            ]
        },
        "answerKey": "B",
    },
)


def _gen2mc_task(
    name: str,
    repo: str,
    split: str,
    revision: str,
    to_doc: Callable[[dict[str, Any]], MCDoc],
    fewshot: Fewshot | None,
) -> MultipleChoiceTask:
    """OLMES's `<name>:rc::gen2mc:xlarge`, scored by accuracy per character on the one split its dataset has (`oe_eval/configs/tasks.py`)."""
    return MultipleChoiceTask(
        name=name,
        dataset=Dataset(
            repo=repo,
            revision=revision,
            subset=None,
            split=split,
        ),
        to_doc=to_doc,
        primary_metric="acc_per_char",
        fewshot=fewshot,
    )


# The few-shot rows are the first 5 of OLMES's `FEWSHOT_SOURCES["OLMES:<task>_mc"]`
# (`oe_eval/tasks/fewshot_sources.py`), in OLMES's own order (its ids are not contiguous), written
# by OLMES and in no split of the dataset, with only the fields `to_doc` reads.
COQA = _gen2mc_task(
    "coqa",
    "allenai/coqa-gen2mc",
    "validation",
    "5e0394a08cc63398a22b745541e5cedf9c171bf3",
    coqa_doc,
    # Zero-shot in OLMES: each prompt already holds a long story.
    None,
)
DROP = _gen2mc_task(
    "drop",
    "allenai/drop-gen2mc",
    "validation",
    "33191ccb7014f77bc6dc3f59792acd43f658eb06",
    drop_doc,
    Fewshot(rows=_DROP_SHOTS),
)
JEOPARDY = _gen2mc_task(
    "jeopardy",
    "allenai/jeopardy-gen2mc",
    "test",
    "0bbd389863e56b304b22b66106caf72566144aa8",
    jeopardy_doc,
    Fewshot(rows=_JEOPARDY_SHOTS),
)
NATURALQS = _gen2mc_task(
    "naturalqs",
    "allenai/nq-gen2mc",
    "validation",
    "a7e553744f28cb673bf25b734513c41e3f413840",
    naturalqs_doc,
    Fewshot(rows=_NATURALQS_SHOTS),
)
SQUAD = _gen2mc_task(
    "squad",
    "allenai/squad-gen2mc",
    "validation",
    "e80f601243d8ed28732c5073cea1092aa4b8bc09",
    squad_doc,
    Fewshot(rows=_SQUAD_SHOTS),
)
