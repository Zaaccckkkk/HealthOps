# RAG retrieval example, end to end

One complete retrieval flow from a question to the retrieved context to the verified answer, recorded on 2026-10-19 against the local stack (`make rag` corpus, `qwen3:8b` and `nomic-embed-text` through Ollama on a Mac). The raw API response is [`src/api-service/examples/reference-lookup.synthetic.json`](../src/api-service/examples/reference-lookup.synthetic.json); the ingest log is [`rag-ingest-run.log`](../src/api-service/examples/rag-ingest-run.log). Synthetic user; the `lookup_reference` tool reads nothing about the user.

## 1. Question (POST /v1/assistant/ask)

```json
{
  "question": "How much sleep do public guidelines recommend for adults?",
  "timezone": "America/New_York"
}
```

## 2. The model chooses the tool and writes the query

| Turn | Tool | Arguments | Status |
|---|---|---|---|
| 1 | `lookup_reference` | `{"query": "sleep recommendations for adults"}` | ok |
| 2 | `state_findings` | `{"findings": ["reference e1.r1", "reference e1.r2", "reference e1.r3"]}` | ok |

The query text is the model's choice; dates, identity and the provider are injected by the server and the model never sees the user id.

## 3. Retrieved context (pgvector cosine search, top 3 above 0.55)

| Rank | Source | Section | Licence | Quote limit | Similarity |
|---|---|---|---|---|---|
| 1 | Recommended Amount of Sleep for a Healthy Adult: joint consensus statement (J Clin Sleep Med 2015;11(6)) (American Academy of Sleep Medicine and Sleep Research Society) | page 1 | Copyright AASM; publicly hosted PDF | 300 chars | 0.7048 |
| 2 | Recommended Amount of Sleep for a Healthy Adult: joint consensus statement (J Clin Sleep Med 2015;11(6)) (American Academy of Sleep Medicine and Sleep Research Society) | page 1 | Copyright AASM; publicly hosted PDF | 300 chars | 0.7013 |
| 3 | Healthy Sleep (MedlinePlus) (National Library of Medicine, NIH) | How much sleep do I need? | Public domain for NLM-authored text; linked third-party content excluded | 1800 chars | 0.6900 |

**Passage 1** (`aasm-srs-sleep-2015`, [https://aasm.org/resources/pdf/pressroom/adult-sleep-duration-consensus.pdf](https://aasm.org/resources/pdf/pressroom/adult-sleep-duration-consensus.pdf)), already cut to the licence limit:

> • People concerned they are sleeping too little or too
> much should consult their healthcare provider. Recommended Amount of Sleep for a Healthy Adult: A Joint
> Consensus Statement of the American Academy of Sleep
> Medicine and Sleep Research Society
> Consensus Conference Panel: Nathaniel F.

**Passage 2** (`aasm-srs-sleep-2015`, [https://aasm.org/resources/pdf/pressroom/adult-sleep-duration-consensus.pdf](https://aasm.org/resources/pdf/pressroom/adult-sleep-duration-consensus.pdf)), already cut to the licence limit:

> 591 Journal of Clinical Sleep Medicine, Vol. 11, No. 6, 2015
> Sleep is essential for optimal health. The American Academy
> of Sleep Medicine (AASM) and Sleep Research Society (SRS)
> developed a consensus recommendation for the amount
> of sleep needed to promote optimal health in adults, using
> a modifi e...

**Passage 3** (`medlineplus-healthy-sleep`, [https://medlineplus.gov/healthysleep.html](https://medlineplus.gov/healthysleep.html)), already cut to the licence limit:

> The amount of sleep you need depends on your age, lifestyle, health, and sleep patterns. The general recommendations for sleep are:
> Babies (4-12 months): 12-16 hours a day (including naps)
> Children (1-2 years): 11-14 hours a day (including naps)
> Children (3-5 years): 10-13 hours a day (including naps)
> Children (6-12 years): 9-12 hours a day
> Teens (13-18 years): 8-10 hours a day
> Adults (18 years and older): 7-9 hours a day
> During puberty, teenagers' biological clocks shift. They tend to fall asleep later and wake up later, which can conflict with the early school start times. This is one reason many teenagers don't get enough sleep.
> Some people think adults need less sleep as they age, but there is no evidence to support this. However, older adults often get less sleep or spend less time in the deep, restful stages of sleep. They also wake up more easily.
> It's not just the number of hours of sleep you get that matters. The quality of the sleep you get is also important. People whose sleep is frequently interrupted might not get enough of certain stages of sleep.
> If you are wondering whether you are getting enough sleep, including quality sleep, ask yourself:
> Do I have trouble getting up in the morning?
> Do I have trouble focusing during the day?
> Do I doze off during the day?
> If you answered yes to any of these questions, you may need to improve your sleep habits.

## 4. Cards given to the model

| Evidence id | Kind | Publisher | Similarity |
|---|---|---|---|
| `e1.r1` | reference | American Academy of Sleep Medicine and Sleep Research Society | 0.7048 |
| `e1.r2` | reference | American Academy of Sleep Medicine and Sleep Research Society | 0.7013 |
| `e1.r3` | reference | National Library of Medicine, NIH | 0.69 |

The model then calls `state_findings` with references only (`reference e1.r1`, …); it writes no sentences and no numbers.

## 5. Verified answer (rendered by Python templates)

Status: `answered` · explanation: `verified` · claims valid: 3/3

```text
Recommended Amount of Sleep for a Healthy Adult: joint consensus statement (J Clin Sleep Med 2015;11(6)) (American Academy of Sleep Medicine and Sleep Research Society, page 1) says: "• People concerned they are sleeping too little or too
much should consult their healthcare provider. Recommended Amount of Sleep for a Healthy Adult: A Joint
Consensus Statement of the American Academy of Sleep
Medicine and Sleep Research Society
Consensus Conference Panel: Nathaniel F." Source: https://aasm.org/resources/pdf/pressroom/adult-sleep-duration-consensus.pdf. This is general reference material, not an assessment of your own values. Recommended Amount of Sleep for a Healthy Adult: joint consensus statement (J Clin Sleep Med 2015;11(6)) (American Academy of Sleep Medicine and Sleep Research Society, page 1) says: "591 Journal of Clinical Sleep Medicine, Vol. 11, No. 6, 2015
Sleep is essential for optimal health. The American Academy
of Sleep Medicine (AASM) and Sleep Research Society (SRS)
developed a consensus recommendation for the amount
of sleep needed to promote optimal health in adults, using
a modifi e..." Source: https://aasm.org/resources/pdf/pressroom/adult-sleep-duration-consensus.pdf. This is general reference material, not an assessment of your own values. Healthy Sleep (MedlinePlus) (National Library of Medicine, NIH, How much sleep do I need?) says: "The amount of sleep you need depends on your age, lifestyle, health, and sleep patterns. The general recommendations for sleep are:
Babies (4-12 months): 12-16 hours a day (including naps)
Children (1-2 years): 11-14 hours a day (including naps)
Children (3-5 years): 10-13 hours a day (including naps)
Children (6-12 years): 9-12 hours a day
Teens (13-18 years): 8-10 hours a day
Adults (18 years and older): 7-9 hours a day
During puberty, teenagers' biological clocks shift. They tend to fall asleep later and wake up later, which can conflict with the early school start times. This is one reason many teenagers don't get enough sleep.
Some people think adults need less sleep as they age, but there is no evidence to support this. However, older adults often get less sleep or spend less time in the deep, restful stages of sleep. They also wake up more easily.
It's not just the number of hours of sleep you get that matters. The quality of the sleep you get is also important. People whose sleep is frequently interrupted might not get enough of certain stages of sleep.
If you are wondering whether you are getting enough sleep, including quality sleep, ask yourself:
Do I have trouble getting up in the morning?
Do I have trouble focusing during the day?
Do I doze off during the day?
If you answered yes to any of these questions, you may need to improve your sleep habits." Source: https://medlineplus.gov/healthysleep.html. This is general reference material, not an assessment of your own values.
```

## 6. What makes this verifiable

- Each `reference e1.r<k>` finding is checked against the cards: the id must exist and be a reference card; a reference claim pointing at a data card is invalid.
- The quote is the stored chunk cut at a sentence boundary to the source's quotation limit (1800 characters for public-domain and Creative Commons sources, 300 for copyrighted documentation), never a model-written paraphrase.
- The answer ends every quote with the fixed sentence that it is general reference material, not an assessment of the user's values; questions that ask to judge the user's own values against a guideline are declined as medical advice.
- `numeric_consistency` reports `unmatched` here because the quoted text contains years and hour ranges that are not numeric cards; it is a diagnostic for data answers, not a verification failure.
