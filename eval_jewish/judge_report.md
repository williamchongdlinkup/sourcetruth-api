## Jewish Corpus — Realistic Query Track: LLM-as-Judge Results

**Chunk nDCG@5**: passage-level relevance (0–2 scale per chunk, nDCG@5).  
**Source nDCG@5**: source text (book/tractate) relevance judged independently (same scale).

### Aggregate nDCG@5
| System | Chunk nDCG@5 | Source nDCG@5 | Source−Chunk |
|---|--:|--:|--:|
| Dense | 0.7415 | 0.8064 | +0.0649 |
| Dense (text-dedup) | 0.6560 | 0.7555 | +0.0995 |
| Dense Two-Level | 0.7320 | 0.7555 | +0.0235 |
| Dense+MMR | 0.7205 | 0.7923 | +0.0718 |
| Hybrid RRF | 0.7415 | 0.8064 | +0.0649 |
| FTS-only | 0.0000 | 0.0000 | +0.0000 |

### Per-query results

#### real-t-01: What does Genesis say about the creation of the world and the unique role of humanity within it?
*Category: tanakh_doctrinal*  |  **Analysis**: # Retrieval Analysis

**Quality Assessment:** The system performed well, returning 4 of 5 directly relevant results. Genesis 1–2 (results 1–2) directly answer the creation narrative portion of the query with high precision. Results 4–5 address humanity's unique role through the "image of God" concept, though with diminishing directness.

**Error Type:** Result 3 (Pirkei Avot 6:11) represents a minor granularity/relevance mismatch—it addresses purpose of creation generally rather than the specific Genesis creation account and humanity's role, introducing tangential theological commentary.

**Notable Pattern:** The system successfully bridged the Tanakh-Mishnah divide, pulling foundational Torah passages (Genesis 1–2) alongside rabbinical elaboration (Pirkei Avot) that deepens the theological understanding of humanity's status. This cross-corpus integration is appropriate for the query's scope, though the Mishnah results (3–4) function as supportive interpretation rather than primary sources.

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.6232 | 0.8066 |
| Dense (text-dedup) | 0.5000 | 0.8066 |
| Dense Two-Level | 0.6011 | 0.8066 |
| Dense+MMR | 0.4530 | 0.8066 |
| Hybrid RRF | 0.6232 | 0.8066 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**1**] *Genesis* (Genesis 1) [Torah] — In the beginning God created the heaven and the earth. Now the earth was unformed and void, and darkness was upon the fa…
- [**2**] *Genesis* (Genesis 2) [Torah] — And the heaven and the earth were finished, and all the host of them. And on the seventh day God finished His work which…
- [**1**] *Pirkei Avot* (Pirkei Avot 6:11) [Mishnah] — Everything that the Holy one Blessed be He created in this world, He created only for His honor, as it is written (Isaia…
- [**1**] *Pirkei Avot* (Pirkei Avot 3:14) [Mishnah] — He was wont to say: "Beloved is man, who was created in the image (of G-d)." Additional love was made known to him, that…
- [**0**] *Isaiah* (Isaiah 44) [Prophets] — Yet now hear, O Jacob My servant, And Israel, whom I have chosen; Thus saith the LORD that made thee, And formed thee fr…

#### real-t-02: How is the covenant between God and Abraham described in the Torah, and what does it require of Abraham's descendants?
*Category: tanakh_doctrinal*  |  **Analysis**: # Evaluation Analysis

**Overall Performance:** The system found **good answers** with 4 of 5 results directly addressing the covenant query. Genesis 17, 15, and 22 are the three primary biblical passages on Abraham's covenant, making their inclusion highly appropriate.

**Error Analysis:** Result 3 (Pirkei Avot 5:2) represents a **topic drift error**—while it discusses God's patience across generations, it is tangential to the Abraham-covenant question and drawn from the Mishnah rather than the Torah. This is a minor inclusion error, not a retrieval failure.

**Notable Patterns:** The system successfully retrieved from the correct corpus (Torah/Genesis) for a Torah-specific query, demonstrating strong primary-source alignment. The single Mishnah inclusion shows the system operates across corpora but occasionally conflates loosely related content. The results span the major Abraham covenant passages (Genesis 15, 17, 22) without granularity issues—all are chapter-level or thematically coherent selections rather than verse-level noise.

**Strengths:** Dense semantic retrieval effectively captured covenant-related vocabulary and theological concepts across multiple

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.6611 | 0.6731 |
| Dense (text-dedup) | 0.6611 | 0.6731 |
| Dense Two-Level | 0.6866 | 0.6731 |
| Dense+MMR | 0.7452 | 0.6611 |
| Hybrid RRF | 0.6611 | 0.6731 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Genesis* (Genesis 17) [Torah] — And when Abram was ninety years old and nine, the LORD appeared to Abram, and said unto him: ‘I am God Almighty; walk be…
- [**0**] *Exodus* (Exodus 34) [Torah] — And the LORD said unto Moses: ‘Hew thee two tables of stone like unto the first; and I will write upon the tables the wo…
- [**0**] *Pirkei Avot* (Pirkei Avot 5:2) [Mishnah] — Ten generations from Adam until Noach. To apprise us how long-suffering He is. For all of these generations they kept on…
- [**1**] *Genesis* (Genesis 22) [Torah] — And it came to pass after these things, that God did prove Abraham, and said unto him: ‘Abraham’; and he said: ‘Here am …
- [**1**] *Genesis* (Genesis 15) [Torah] — After these things the word of the LORD came unto Abram in a vision, saying: ‘Fear not, Abram, I am thy shield, thy rewa…

#### real-t-03: What does Exodus say about the theophany at Mount Sinai and the content of the Ten Commandments?
*Category: tanakh_doctrinal*  |  **Analysis**: # Retrieval Analysis

**Success Assessment:** The system performed well, retrieving highly relevant passages that directly address both components of the query. Results 2 and 3 are particularly strong—Exodus 19 covers the theophany narrative (preparation, divine presence, thunder/fire), while Exodus 20 presents the complete Decalogue verbatim.

**Error Type:** No significant failure occurred. Results 1 and 4 represent minor granularity misalignment—they address the *replacement* tablets (Exodus 34, Deuteronomy 10) rather than the original Sinai event, though this reflects topical relevance to the broader Ten Commandments discussion. Result 5 (Deuteronomy 5) is a partial answer, restating the commandments but omitting the theophanic context.

**Notable Pattern:** The system retrieved only from the Torah (Exodus and Deuteronomy), which is appropriate—the query is inherently Torah-specific, and no Mishnaic or other cross-corpus material was incorrectly surfaced. The inclusion of Deuteronomy repetitions (results 4–5) reflects

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.5848 | 0.8066 |
| Dense (text-dedup) | 0.5000 | 0.8066 |
| Dense Two-Level | 0.5848 | 0.8066 |
| Dense+MMR | 0.5620 | 0.8066 |
| Hybrid RRF | 0.5848 | 0.8066 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**1**] *Exodus* (Exodus 34) [Torah] — And the LORD said unto Moses: ‘Hew thee two tables of stone like unto the first; and I will write upon the tables the wo…
- [**1**] *Exodus* (Exodus 19) [Torah] — In the third month after the children of Israel were gone forth out of the land of Egypt, the same day came they into th…
- [**2**] *Exodus* (Exodus 20) [Torah] — And God spoke all these words, saying: I am the LORD thy God, who brought thee out of the land of Egypt, out of the hous…
- [**1**] *Deuteronomy* (Deuteronomy 10) [Torah] — At that time the LORD said unto me: ‘Hew thee two tables of stone like unto the first, and come up unto Me into the moun…
- [**1**] *Deuteronomy* (Deuteronomy 5) [Torah] — And Moses called unto all Israel, and said unto them: Hear, O Israel, the statutes and the ordinances which I speak in y…

#### real-t-04: How does the Torah describe the sanctity of the Sabbath — where is it commanded and what does observance entail?
*Category: tanakh_doctrinal*  |  **Analysis**: # Evaluation Analysis

**Overall Performance: Strong Partial Success**

The system successfully retrieved relevant Torah passages addressing Sabbath sanctity and commands, with Exodus 31 and 35 directly covering Sabbath observance prohibitions and Leviticus 23 addressing holy convocations. However, the results suffer from a **granularity mismatch**: the query asks "where is it commanded," and while chapter-level results are provided, the system did not surface the most foundational passages—Exodus 20:8–11 and Deuteronomy 5:12–15, which contain the explicit Ten Commandments formulations of the Sabbath law. This represents a **vocabulary/indexing gap** where highly specific divine commands may have been deprioritized in favor of broader legislative contexts.

**Error Pattern: Corpus-Appropriate but Incomplete Coverage**

All results correctly remain within the Torah (Tanakh), demonstrating appropriate source retrieval for a question framed within Jewish scripture. The notable absence of Exodus 20 (the primary Sabbath commandment in the Decalogue) suggests the dense semantic retriever may have weighted holistic descriptions of

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.6119 | 0.6146 |
| Dense (text-dedup) | 0.4427 | 0.6146 |
| Dense Two-Level | 0.6901 | 0.6146 |
| Dense+MMR | 0.5000 | 0.5848 |
| Hybrid RRF | 0.6119 | 0.6146 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**1**] *Leviticus* (Leviticus 25) [Torah] — And the LORD spoke unto Moses in mount Sinai, saying: Speak unto the children of Israel, and say unto them: When ye come…
- [**0**] *Exodus* (Exodus 31) [Torah] — And the LORD spoke unto Moses, saying: ’See, I have called by name Bezalel the son of Uri, the son of Hur, of the tribe …
- [**1**] *Deuteronomy* (Deuteronomy 5) [Torah] — And Moses called unto all Israel, and said unto them: Hear, O Israel, the statutes and the ordinances which I speak in y…
- [**2**] *Exodus* (Exodus 35) [Torah] — And Moses assembled all the congregation of the children of Israel, and said unto them: ‘These are the words which the L…
- [**2**] *Leviticus* (Leviticus 23) [Torah] — And the LORD spoke unto Moses, saying: Speak unto the children of Israel, and say unto them: The appointed seasons of th…

#### real-t-05: How does the book of Isaiah describe the future redemption of Israel and the prophetic vision of peace at the end of days?
*Category: tanakh_prophets*  |  **Analysis**: # Retrieval Analysis

**Success Level:** The system found good answers with one notable inclusion error.

Results 1, 3, and 4 directly address the query, providing Isaiah's and Jeremiah's eschatological visions of redemption and end-times peace—Isaiah 2 and Jeremiah 30–31 are canonical sources for this topic. Result 2 (Micah 4) represents a **wrong source retrieval** error: while thematically parallel to Isaiah 2, Micah was not requested and displaces a more directly relevant Isaiah passage. Result 5 (Isaiah 49) is a **granularity mismatch**—it contains servant-song material relevant to Isaiah's redemptive theology but lacks the specific "end of days" framing central to the query.

**Error Pattern:** The system prioritized semantic similarity over source specificity, retrieving a non-Isaiah prophet (Micah) in the top 5 when the query explicitly names Isaiah. This suggests the dense retrieval model weights thematic resonance (mountain of the LORD, eschatological peace) equally across the Tanakh rather than respecting source constraints. The corpus

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.6696 | 0.7346 |
| Dense (text-dedup) | 0.6696 | 0.6696 |
| Dense Two-Level | 0.6696 | 0.6696 |
| Dense+MMR | 0.7352 | 0.6952 |
| Hybrid RRF | 0.6696 | 0.7346 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Isaiah* (Isaiah 2) [Prophets] — The word that Isaiah the son of Amoz saw concerning Judah and Jerusalem. And it shall come to pass in the end of days, T…
- [**1**] *Micah* (Micah 4) [Prophets] — But in the end of days it shall come to pass, That the mountain of the LORD’S house shall be established as the top of t…
- [**1**] *Jeremiah* (Jeremiah 31) [Prophets] — In those days, the word of the LORD, I will be unto thee a God, for all families of Israel, and they will be unto me a p…
- [**1**] *Jeremiah* (Jeremiah 30) [Prophets] — The word that came to Jeremiah from the LORD, saying: ’Thus speaketh the LORD, the God of Israel, saying: Write thee all…
- [**1**] *Isaiah* (Isaiah 49) [Prophets] — Listen, O isles, unto me, And hearken, ye peoples, from far: The LORD hath called me from the womb, From the bowels of m…

#### real-t-06: What does Jeremiah say about the new covenant that God will establish with the house of Israel?
*Category: tanakh_prophets*  |  **Analysis**: # Evaluation Analysis

The system **found good answers** — results 1, 2, and 5 all contain relevant Jeremiah passages about covenants with Israel. Results 1 and 2 directly address the query's core topic: Jeremiah 31 explicitly discusses God's covenant with the house of Israel ("I will be unto thee a God, for all families of Israel"), and Jeremiah 33 appears to cover the establishment of that covenant. Result 5 (Jeremiah 11) discusses covenant terms more broadly, still maintaining relevance.

A **granularity mismatch** is present but minor: the snippets are chapter-level abstracts rather than verse-specific excerpts, making it difficult to pinpoint exact statements about the "new covenant" concept without consulting the full text. Result 3 and 4 introduce an **off-topic error** by retrieving Isaiah passages unrelated to Jeremiah or covenants with Israel, suggesting the system confused thematic resonance (divine promise language) with the specific query focus.

No cross-corpus gap is evident here — the query targets the Tanakh (Prophets) and the system appropriately

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.5000 | 1.0000 |
| Dense (text-dedup) | 0.5000 | 1.0000 |
| Dense Two-Level | 0.5000 | 1.0000 |
| Dense+MMR | 0.5000 | 1.0000 |
| Hybrid RRF | 0.5000 | 1.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**1**] *Jeremiah* (Jeremiah 31) [Prophets] — In those days, the word of the LORD, I will be unto thee a God, for all families of Israel, and they will be unto me a p…
- [**0**] *Jeremiah* (Jeremiah 33) [Prophets] — Moreover the word of the LORD came unto Jeremiah the second time, while he was yet shut up in the court of the guard, sa…
- [**0**] *Isaiah* (Isaiah 65) [Prophets] — I gave access to them that asked not for Me, I was at hand to them that sought Me not; I said: ‘Behold Me, behold Me’, u…
- [**0**] *Isaiah* (Isaiah 66) [Prophets] — Thus saith the LORD: The heaven is My throne, and the earth is My footstool; where is the house that ye may build unto M…
- [**0**] *Jeremiah* (Jeremiah 11) [Prophets] — The word that came to Jeremiah from the LORD, saying: ’Hear ye the words of this covenant, and speak unto the men of Jud…

#### real-t-07: How does the prophet Amos describe God's demand for social justice and his critique of Israel's hollow religious observance?
*Category: tanakh_prophets*  |  **Analysis**: # Retrieval Analysis

**Overall Performance: Partial Success**

The system retrieved relevant topical content but with significant precision issues. Results 1, 4, and 5 are directly on-topic—Amos 4 explicitly condemns oppression of the poor and needy, and Amos 8 addresses God's judgment—yet results 2 and 3 (Micah and Hosea) are topically related but not specifically addressing Amos's voice or social justice critique. This suggests a **vocabulary/semantic mismatch** where the retriever conflated prophetic social justice discourse across multiple books rather than prioritizing the queried prophet.

**Error Type: Partial Topic Coverage + Granularity Issues**

The system found Amos passages but failed to surface the most explicit anti-ritualism content. The query specifically asks about "hollow religious observance," yet none of the top 5 results include Amos 5:21–24 (God's rejection of empty sacrifices and demand for justice to "roll down like waters"), which directly answers both dimensions of the question. This represents a **granularity and vocabulary mismatch**—the

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.7572 | 0.7184 |
| Dense (text-dedup) | 0.7184 | 0.7184 |
| Dense Two-Level | 0.7766 | 0.7184 |
| Dense+MMR | 0.7486 | 0.7184 |
| Hybrid RRF | 0.7572 | 0.7184 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Amos* (Amos 8) [Prophets] — Thus the Lord GOD showed me; and behold a basket of summer fruit. And He said: ‘Amos, what seest thou?’ And I said: ‘A b…
- [**1**] *Micah* (Micah 3) [Prophets] — And I said: Hear, I pray you, ye heads of Jacob, and rulers of the house of Israel: Is it not for you to know justice? W…
- [**0**] *Hosea* (Hosea 3:1) [Prophets] — And the LORD said unto me: ‘Go yet, love a woman beloved of her friend and an adulteress, even as the LORD loveth the ch…
- [**2**] *Amos* (Amos 4) [Prophets] — Hear this word, ye kine of Bashan, That are in the mountain of Samaria, That oppress the poor, that crush the needy, Tha…
- [**1**] *Micah* (Micah 6) [Prophets] — Hear ye now what the LORD saith: Arise, contend thou before the mountains, And let the hills hear thy voice. Hear, O ye …

#### real-t-08: What does the book of Psalms say about God's protection of the righteous and the experience of divine nearness?
*Category: tanakh_writings*  |  **Analysis**: # Evaluation Analysis

**Retrieval Success:** The system found **excellent answers**. All five results are Psalms passages directly addressing both query dimensions—God's protection of the righteous (Psalms 71, 34, 91) and divine nearness (Psalms 71, 73, 18:23).

**Error Analysis:** No meaningful errors occurred. The vocabulary and conceptual alignment is strong: "refuge," "shelter," "deliver," "trust," and "shadow of the Almighty" directly match the query's semantic content around protection and proximity to God.

**Notable Pattern:** The results demonstrate appropriate **corpus and granularity consistency**—all five are from the Writings (Psalms specifically), which is the correct source for this query. The system retrieved full Psalm citations (or near-complete passages) rather than fragmented verses, providing sufficient context to evaluate theological content. The diverse selection of Psalms (71, 34, 73, 18, 91) avoids redundancy while maintaining topical coherence, suggesting strong semantic ranking.

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.9152 | 0.8066 |
| Dense (text-dedup) | 0.6952 | 0.7184 |
| Dense Two-Level | 0.7766 | 0.7184 |
| Dense+MMR | 0.9152 | 1.0000 |
| Hybrid RRF | 0.9152 | 0.8066 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Psalms* (Psalms 71) [Writings] — In Thee, O LORD, have I taken refuge; Let me never be ashamed. Deliver me in Thy righteousness, and rescue me; Incline T…
- [**2**] *Psalms* (Psalms 34) [Writings] — [A Psalm] of David; when he changed his demeanour before Abimelech, who drove him away, and he departed. I will bless th…
- [**1**] *Psalms* (Psalms 73) [Writings] — A Psalm of Asaph. Surely God is good to Israel, even to such as are pure in heart. But as for me, my feet were almost go…
- [**2**] *Psalms* (Psalms 18:23) [Writings] — For all His ordinances were before me, and I put not away His statutes from me. And I was single-hearted with Him, and I…
- [**2**] *Psalms* (Psalms 91) [Writings] — O thou that dwellest in the covert of the Most High, And abidest in the shadow of the Almighty; I will say of the LORD, …

#### real-t-09: What does Proverbs teach about wisdom — its source, its value, and the qualities of a truly wise person?
*Category: tanakh_writings*  |  **Analysis**: # Evaluation Analysis

**Retrieval Quality:**
The system performed very well, returning five directly relevant passages from Proverbs that address the query's core components. Results 1, 2, and 4 explicitly teach wisdom's source (divine/personified), its value (builds houses, delivers from death), and wise person qualities (understanding, uprightness, fear of the LORD). Results 3 and 5 reinforce these themes through contrasts with foolishness.

**Error Type:**
No significant errors occurred. The system correctly identified and retrieved passages from the appropriate book (Proverbs) within the Writings section of the Tanakh, with appropriate granularity (full chapter excerpts rather than scattered verses).

**Notable Pattern:**
The retrieval demonstrates strong semantic alignment without vocabulary mismatch—the system successfully surfaced passages containing key concepts (wisdom, understanding, fear of the LORD, uprightness) that answer the thematic question despite varying specific phrasings across chapters. All results remained within the single corpus (Tanakh) as expected, with no cross-corpus contamination.

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.9344 | 0.7346 |
| Dense (text-dedup) | 0.6696 | 0.6696 |
| Dense Two-Level | 0.8614 | 0.6696 |
| Dense+MMR | 0.7352 | 0.6952 |
| Hybrid RRF | 0.9344 | 0.7346 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Proverbs* (Proverbs 1) [Writings] — The proverbs of Solomon the son of David, king of Israel; To know wisdom and instruction; To comprehend the words of und…
- [**2**] *Proverbs* (Proverbs 8) [Writings] — Doth not wisdom call, And understanding put forth her voice? In the top of high places by the way, Where the paths meet,…
- [**2**] *Proverbs* (Proverbs 14) [Writings] — Every wise woman buildeth her house; But the foolish plucketh it down with her hands. He that walketh in his uprightness…
- [**2**] *Proverbs* (Proverbs 24) [Writings] — Be not thou envious of evil men, Neither desire to be with them. For their heart studieth destruction, And their lips ta…
- [**1**] *Proverbs* (Proverbs 10) [Writings] — The proverbs of Solomon. A wise son maketh a glad father; but a foolish son is the grief of his mother. Treasures of wic…

#### real-t-10: How does the book of Job explore the problem of innocent suffering and challenge conventional views of divine justice?
*Category: tanakh_writings*  |  **Analysis**: # Evaluation Analysis

**Retrieval Success:** The system achieved **partial success**. It correctly identified the Book of Job as the relevant source and retrieved multiple chapters that contain Job's direct expressions of suffering and complaint, which are central to the problem of innocent suffering.

**Error Type — Granularity Mismatch:** The primary limitation is **granularity mismatch**. The query explicitly asks for analytical exploration of *how* Job challenges divine justice and conventional theodicy—a thematic and interpretive question. The system retrieved raw biblical verses (chapters 9, 6, 16, 33, 19) rather than scholarly commentary, interpretive summaries, or passages where the theological argument is most concentrated. For instance, Job 9:2 ("how can man be just with God?") touches the theme but lacks the sustained philosophical challenge found in Job 42 or the divine response that provides counterargument.

**Missing Analytical Layer:** The corpus appears to contain only the biblical text itself without accompanying rabbinic interpretation or secondary sources. A complete answer would require access to midrashic commentary (e.g., from the Mishnah or Talmud) or philosophical discussion

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.7574 | 1.0000 |
| Dense (text-dedup) | 0.4530 | 0.6877 |
| Dense Two-Level | 0.7122 | 0.6877 |
| Dense+MMR | 0.8304 | 1.0000 |
| Hybrid RRF | 0.7574 | 1.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**1**] *Job* (Job 9) [Writings] — Then Job answered and said: Of a truth I know that it is so; And how can man be just with God? If one should desire to c…
- [**2**] *Job* (Job 6) [Writings] — Then Job answered and said: Oh that my vexation were but weighed, and my calamity laid in the balances altogether! For n…
- [**2**] *Job* (Job 16) [Writings] — Then Job answered and said: I have heard many such things; Sorry comforters are ye all. Shall windy words have an end? O…
- [**1**] *Job* (Job 33) [Writings] — Howbeit, Job, I pray thee, hear my speech, And hearken to all my words. Behold now, I have opened my mouth, My tongue ha…
- [**2**] *Job* (Job 19) [Writings] — Then Job answered and said: How long will ye vex my soul, And crush me with words? These ten times have ye reproached me…

#### real-m-01: What does the Mishnah teach about the recitation of the Shema — when must it be said and what constitutes valid fulfillment?
*Category: mishnah_law*  |  **Analysis**: # Evaluation Analysis

**Success Status:** The system achieved strong partial success. All five results are directly from Mishnah Berakhot and address core aspects of the query—timing requirements (results 1, 3, 4) and valid fulfillment conditions (results 2, 3, 5). However, coverage is incomplete: the results foreground *when* Shema must be recited but underrepresent the full scope of what constitutes valid performance.

**Error Type:** Mild granularity mismatch. The system retrieved specific mishnayot rather than a more comprehensive passage synthesizing Shema obligations. Result 2 touches on intent requirements, but nuanced validity criteria (e.g., language requirements, full recitation of all three paragraphs) are absent from the top five, suggesting these details either occupy lower-ranked results or require multiple passages to reconstruct.

**Notable Pattern:** The retrieval remained appropriately within a single source (Mishnah Berakhot) with no cross-corpus contamination or Tanakh intrusions. This demonstrates good source-specificity for a Mishnah-targeted query. The dense semantic system correctly priorit

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.9344 | 0.8066 |
| Dense (text-dedup) | 0.7184 | 0.7184 |
| Dense Two-Level | 0.8614 | 0.7184 |
| Dense+MMR | 0.8614 | 0.7664 |
| Hybrid RRF | 0.9344 | 0.8066 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Berakhot* (Berakhot 2:3) [Mishnah] — One who recites the Shema without causing himself to hear it fulfills the obligation. R. Yossi says: He does not fulfill…
- [**2**] *Berakhot* (Berakhot 2:1) [Mishnah] — If one were reading [the section of the Shema] in the Torah and the time for the recital [of the Shema] arrived — if he …
- [**2**] *Berakhot* (Berakhot 1:2) [Mishnah] — From which time may the Shema be recited in the morning? When it is possible to distinguish between the tcheleth (blue) …
- [**2**] *Berakhot* (Berakhot 1:1) [Mishnah] — From which time may the Shema be recited in the evenings? From the time that the Cohanim have gone in to eat their terum…
- [**1**] *Berakhot* (Berakhot 2:5) [Mishnah] — A bridegroom is exempt from the recital of the Shema the first night, until motzai Shabbath if he had not performed the …

#### real-m-02: How does the Mishnah define the thirty-nine main categories of labor forbidden on the Sabbath?
*Category: mishnah_law*  |  **Analysis**: # Analysis

**Result Quality:** The system found a **partial answer**. Result #1 directly addresses the query by naming the 39 categories (avoth melachoth) and begins listing them (sowing, plowing), which is precisely what the user asked for. However, the truncation cuts off the complete enumeration, leaving the answer incomplete.

**Error Type:** **Granularity mismatch**—the system retrieved Shabbat 7:2, which is the correct source passage, but only the opening lines. The full definition with all 39 categories enumerated likely spans multiple verses or continues beyond what was indexed or returned. Additionally, results #2–5 show **topic drift**, retrieving related but distinct Shabbat-law passages (carrying, plowing liability under multiple commandments, holiday labor) rather than the systematic enumeration.

**Notable Pattern:** The retrieval stayed appropriately within the Mishnah corpus (no cross-corpus contamination), correctly identifying Shabbat as the relevant tractate. However, the dense semantic retrieval appears to have captured thematic relevance (labor and Shabbath) without fully retrieving the complete, structured list the

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.8066 | 1.0000 |
| Dense (text-dedup) | 1.0000 | 1.0000 |
| Dense Two-Level | 0.7346 | 1.0000 |
| Dense+MMR | 1.0000 | 1.0000 |
| Hybrid RRF | 0.8066 | 1.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Shabbat* (Shabbat 7:2) [Mishnah] — The avoth melachoth are forty less one: sowing, plowing [the reason we are not taught "plowing" first, and then "sowing"…
- [**1**] *Shabbat* (Shabbat 1:1) [Mishnah] — The yetzioth [acts of carrying out from one domain to another] of Shabbath [i.e., stated in respect to Shabbath (Hachnas…
- [**0**] *Makkot* (Makkot 3:9) [Mishnah] — It is possible for one to plow a single furrow and to be liable for (transgression of) eight negative commandments. [Thi…
- [**0**] *Moed Katan* (Moed Katan 1:2) [Mishnah] — R. Elazar b. Azaryah says: It is forbidden to make an amah [an irrigation ditch (so called because it is a cubit (amah) …
- [**0**] *Beitzah* (Beitzah 5:2) [Mishnah] — Whatever one is liable for by reason of shvuth ("resting") [i.e., whatever the sages forbade one from doing on Shabbath …

#### real-m-03: What does Pirkei Avot teach about Torah study, acquiring a teacher, and the qualities of a person who fears sin?
*Category: mishnah_ethics*  |  **Analysis**: # Analysis of Dense Semantic Retrieval Results

**Overall Performance:** The system achieved **strong partial success**, retrieving highly relevant passages that directly address two of the three query dimensions (acquiring a teacher and fearing sin), but incompletely covered Torah study methodology.

**Coverage Assessment:**
- ✓ **Acquiring a teacher** (query dimension 2): Result 1 directly quotes Pirkei Avot 1:6 ("Make a teacher for yourself"), the canonical source.
- ✓ **Fearing sin** (query dimension 3): Result 2 explicitly addresses this via Pirkei Avot 2:5's discussion of the "bur" (empty person) who cannot fear sin.
- ◐ **Torah study** (query dimension 1): Result 3 (Pirkei Avot 6:5) lists 48 attributes for acquiring Torah but doesn't deeply explore what Pirkei Avot *teaches* about study itself; Results 4–5 discuss honoring teachers/students rather than study methodology.

**Error Type:** Mild **granularity mismatch**—the

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.8614 | 1.0000 |
| Dense (text-dedup) | 0.7346 | 0.7346 |
| Dense Two-Level | 0.9159 | 0.7346 |
| Dense+MMR | 0.8614 | 0.8066 |
| Hybrid RRF | 0.8614 | 1.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Pirkei Avot* (Pirkei Avot 1:6) [Mishnah] — Yehoshuah ben Prachya and Nitai Ha'arbeli received it from them. Yehoshua ben Prachya says: Make a teacher for yourself.…
- [**2**] *Pirkei Avot* (Pirkei Avot 2:5) [Mishnah] — He was wont to say: "A bur will not fear sin." [A "bur" is one who is "empty" of everything (the Targum of Genesis 47:19…
- [**2**] *Pirkei Avot* (Pirkei Avot 6:5) [Mishnah] — Torah is greater than priesthood and than kingship. For kingship is acquired with thirty eminences and priesthood with t…
- [**1**] *Pirkei Avot* (Pirkei Avot 4:12) [Mishnah] — R. Elazar ben Shamua says: Let the honor of your disciple be as beloved by you as your own. [For thus do we find with Mo…
- [**1**] *Pirkei Avot* (Pirkei Avot 6:3) [Mishnah] — If one learns from his friend one chapter or one halachah or one verse or one word or even one letter, he must accord hi…

#### real-m-04: How does the Mishnah describe the obligation to return lost property — to whom must it be returned and under what conditions?
*Category: mishnah_law*  |  **Analysis**: # Retrieval Analysis

**Quality Assessment:** The system achieved **strong partial success**. Results 1, 2, 3, and 10 directly address core aspects of the query—defining *aveidah*, conditions for return (simanim/identifying marks, location, claimant credibility), and location-dependent obligations. However, result 4 addresses acquisition rules rather than return obligations, and result 5 concerns deposits (*pikadon*) rather than lost property, representing notable false positives.

**Error Type:** The primary issue is **granularity and topical scope creep**. While Bava Metzia 1:3 and 3:9 are from the relevant tractate, they address different legal concepts (acquisition of found objects and bailment liability) rather than the return obligations framework requested. This reflects imperfect semantic discrimination—the system correctly identified the source document but retrieved neighboring passages that share vocabulary ("found," "obligation") without matching the specific legal question.

**Notable Pattern:** All five results correctly stayed within the Mishnah corpus and specifically within Bava Metzia, the appropriate source. There is no cross-corpus contamination or Tan

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.9159 | 1.0000 |
| Dense (text-dedup) | 0.8183 | 0.9675 |
| Dense Two-Level | 1.0000 | 0.9675 |
| Dense+MMR | 0.8327 | 1.0000 |
| Hybrid RRF | 0.9159 | 1.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Bava Metzia* (Bava Metzia 2:9) [Mishnah] — What is an aveidah ( a lost object)? [where it is evident that the owner is unaware if its whereabouts.] If he found an …
- [**2**] *Bava Metzia* (Bava Metzia 2:7) [Mishnah] — If he named the lost object but not its simanim, it should not be returned to him. And if he is (known as) a deceiver, e…
- [**2**] *Bava Metzia* (Bava Metzia 2:10) [Mishnah] — If he found it in the stall, [ even if it is unguarded there, as when the stall is unlocked ], he need not return it. (I…
- [**1**] *Bava Metzia* (Bava Metzia 1:3) [Mishnah] — If he were riding on a beast and saw a metziah (a lost object), and he said to his friend: "Give it to me" — If he (the …
- [**0**] *Bava Metzia* (Bava Metzia 3:9) [Mishnah] — If one deposited a jug with his neighbor and the owner did not designate a place for it [in the house of the watcher, sa…

#### real-m-05: What does the Mishnah say about ona'ah (price fraud or overcharging) and the halakhic principles governing fair market dealings?
*Category: mishnah_law*  |  **Analysis**: # Retrieval Analysis

**Success Level:** The system retrieved **excellent direct answers**. Four of the top five results (items 1–4) are consecutive mishnayot from Bava Metzia 4, which is precisely the primary source text governing ona'ah. Item 5 (Bava Batra 5:6) provides complementary halakhic principles on sales defects, representing appropriate secondary contextualization.

**Error Type:** No significant error occurred. The retrieval demonstrates proper **source matching** (Mishnah for a Mishnah query) with correct **granularity** (relevant mishnayot rather than disconnected passages). The results progress logically through ona'ah thresholds (4:3), eligibility of claimants (4:4), ona'ah of words (4:10), and restatement of principles (4:7).

**Notable Pattern:** The system exhibits **intra-corpus coherence**—all results remain within rabbinic literature (Mishnah) rather than mixing Tanakh sources, despite item 4's citation of Leviticus 25:17. This demonstrates intelligent handling of embedded

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.9152 | 0.8183 |
| Dense (text-dedup) | 0.8183 | 0.8183 |
| Dense Two-Level | 0.9024 | 0.8183 |
| Dense+MMR | 0.8930 | 0.8098 |
| Hybrid RRF | 0.9152 | 0.8183 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Bava Metzia* (Bava Metzia 4:3) [Mishnah] — Ona'ah ("Wronging") is four [ma'ah of] silver [of six ma'ah to a dinar, a sela being four dinars], of twenty-four [ma'ah…
- [**2**] *Bava Metzia* (Bava Metzia 4:4) [Mishnah] — Both the buyer and the seller can claim ona'ah. Just as a non-merchant can claim ona'ah, so can a merchant. R. Yehudah s…
- [**1**] *Bava Metzia* (Bava Metzia 4:10) [Mishnah] — Just as there is ona'ah in buying and selling, so there is ona'ah in words, [it being written (Leviticus 25:17): "And yo…
- [**2**] *Bava Metzia* (Bava Metzia 4:7) [Mishnah] — Ona'ah is four (ma'ah of) silver [for the purchase of a sela, twenty-four ma'ah of silver, whereby ona'ah is found to be…
- [**2**] *Bava Batra* (Bava Batra 5:6) [Mishnah] — There are four "measures" [distinct laws] in respect to sales: If he sold him good wheat and it was found to be bad, the…

#### real-x-01: What does Jewish scripture teach about honoring one's parents — both the biblical commandment and how the Mishnah interprets it?
*Category: cross_corpus*  |  **Analysis**: # Evaluation Analysis

**Overall Performance:** The system achieved *partial success* but missed the core biblical component. Results 1 and 5 directly address Mishnaic interpretation of honoring parents (Sanhedrin 7:8 on curses; Nedarim 9:1 explicitly on "honor of father and mother"), satisfying roughly half the query. However, Results 2–4 are off-topic ethical teachings about disciples and Torah study with only tangential relevance to parental honor.

**Error Type:** This is primarily a *cross-corpus gap* failure. The query explicitly requests both "biblical commandment and Mishnah interpretation," yet no results from Tanakh (the biblical source) appear. The Exodus 17:9 reference in Result 2 is merely incidental. The system should have surfaced Exodus 20:12 or Deuteronomy 5:16 (the foundational commandments on honoring parents) before or alongside the Mishnaic material.

**Notable Pattern:** The dense retrieval successfully identified Mishnah-specific sources but failed to cross into the Tanakh corpus for the biblical half of the

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.4252 | 0.5541 |
| Dense (text-dedup) | 0.4599 | 0.5541 |
| Dense Two-Level | 0.5000 | 0.5541 |
| Dense+MMR | 0.5000 | 0.5848 |
| Hybrid RRF | 0.4252 | 0.5541 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**1**] *Sanhedrin* (Sanhedrin 7:8) [Mishnah] — One who desecrates the Sabbath through an act where witting transgression is subject to kareth, and unwitting transgress…
- [**0**] *Pirkei Avot* (Pirkei Avot 4:12) [Mishnah] — R. Elazar ben Shamua says: Let the honor of your disciple be as beloved by you as your own. [For thus do we find with Mo…
- [**0**] *Pirkei Avot* (Pirkei Avot 1:1) [Mishnah] — Moses received Torah from Sinai. [I say that because this tractate is not founded on any explanation of a mitzvah of the…
- [**0**] *Pirkei Avot* (Pirkei Avot 4:6) [Mishnah] — R. Yossi says: If one honors the Torah, his body is honored by men. [If one expounds the "defective" and "superfluous" (…
- [**1**] *Nedarim* (Nedarim 9:1) [Mishnah] — R. Eliezer says: Honor of father and mother can be used as an opening (to absolve one of a vow) [i.e., saying to him: "H…

#### real-x-02: How do the Tanakh and the Mishnah together address the obligation to care for the poor, the widow, and the stranger?
*Category: cross_corpus*  |  **Analysis**: # Analysis of Dense Semantic Retrieval Results

**Performance Assessment:** The system achieved **partial success** — it retrieved topically relevant sources but failed to comprehensively address the query's cross-corpus scope. Results 1–2 and 5 contain legitimate material on poor-relief obligations (hefker for the poor, precedence of poverty prevention), but the corpus gap problem is severe.

**Error Type — Cross-Corpus Gap:** The query explicitly asks how "Tanakh *and* Mishnah *together*" address the obligation, yet only 2 of 5 results are Mishnaic passages. Result 3 (Deuteronomy 24) is a Tanakh passage about *divorce*, not poverty or care for vulnerable populations — a **vocabulary/conceptual mismatch**. Result 4 (Pirkei Avot 1:1) is completely off-topic. The system retrieved from the Jewish corpus but failed to surface complementary Tanakh passages (e.g., Exodus 22:21–23 on strangers/widows, Leviticus 19:9–10 on gleaning) that would directly

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.6309 | 0.6232 |
| Dense (text-dedup) | 0.6309 | 0.6232 |
| Dense Two-Level | 0.4675 | 0.6232 |
| Dense+MMR | 0.5000 | 0.5500 |
| Hybrid RRF | 0.6309 | 0.6232 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**0**] *Bava Metzia* (Bava Metzia 2:11) [Mishnah] — His aveidah or the aveidah of his father — his lost object takes precedence, [it being written (Deuteronomy 15:4): "But …
- [**2**] *Eduyot* (Eduyot 4:3) [Mishnah] — Beth Shammai say: Hefker for the poor is hefker. [If one made (his produce) hefker (i.e., "renounced") for the poor but …
- [**0**] *Deuteronomy* (Deuteronomy 24) [Torah] — When a man taketh a wife, and marrieth her, then it cometh to pass, if she find no favour in his eyes, because he hath f…
- [**0**] *Pirkei Avot* (Pirkei Avot 1:1) [Mishnah] — Moses received Torah from Sinai. [I say that because this tractate is not founded on any explanation of a mitzvah of the…
- [**0**] *Makkot* (Makkot 3:1) [Mishnah] — And these are the ones who receive stripes [Not only "these." For the tanna teaches (these) and omits many who receive s…

#### real-x-03: What does Jewish tradition teach about repentance (teshuvah) — in both the prophetic writings and Mishnaic halakha?
*Category: cross_corpus*  |  **Analysis**: # Retrieval Analysis: Teshuvah Query

**Overall Performance: Partial Success**
The system successfully retrieved relevant material from both required corpora—one strong Mishnaic halakha result (Yoma 8:9) and three prophetic passages on repentance (Hosea, Jeremiah, Isaiah)—demonstrating competent cross-corpus coverage. However, the results show a **granularity mismatch**: results 3–5 return entire chapters rather than specific verses addressing teshuvah, making them less precise than the targeted Yoma passage.

**Error Pattern**
The primary issue is **vocabulary mismatch combined with granularity mismatch**. While Jeremiah 3 and Isaiah 1 contain relevant repentance theology, the system retrieved full chapters without isolating the specific passages (e.g., Jeremiah 3:22, Isaiah 1:16–18) that directly teach about returning to God. Result 5 (Hosea 14) is particularly problematic—it appears to excerpt punitive language rather than the chapter's actual repentance call.

**Notable Pattern**
The system successfully balanced

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.8422 | 0.8183 |
| Dense (text-dedup) | 0.8183 | 0.8183 |
| Dense Two-Level | 0.9344 | 0.8183 |
| Dense+MMR | 0.7963 | 0.7766 |
| Hybrid RRF | 0.8422 | 0.8183 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**2**] *Yoma* (Yoma 8:9) [Mishnah] — If one says: "I shall sin and repent; I shall sin and repent," [twice], he is not given the wherewithal to repent, [for …
- [**2**] *Hosea* (Hosea 6) [Prophets] — ’Come, and let us return unto the LORD; For He hath torn, and He will heal us, He hath smitten, and He will bind us up. …
- [**1**] *Jeremiah* (Jeremiah 3) [Prophets] — . . . saying: If a man put away his wife, and she go from him, and become another man’s, may he return unto her again? W…
- [**1**] *Isaiah* (Isaiah 1) [Prophets] — The Vision of Isaiah the son of Amoz, which he saw concerning Judah and Jerusalem, in the days of Uzziah, Jotham, Ahaz, …
- [**2**] *Hosea* (Hosea 14) [Prophets] — Samaria shall bear her guilt, For she hath rebelled against her God; They shall fall by the sword; Their infants shall b…

#### real-neg-01: What does the Tanakh say about nuclear energy, artificial intelligence, and the ethics of modern technology? *(negative / out-of-scope)*
*Category: negative*  |  **Analysis**: # Retrieval Analysis

**Result Quality: Complete Failure**

The system failed to retrieve any substantive answers to the query. The root cause is a **topic absence error** — the Tanakh contains no direct discussion of nuclear energy, artificial intelligence, or modern technology, as these concepts postdate the text by millennia. This is a fundamental corpus limitation, not a retrieval system defect.

**Secondary Error: Cross-Corpus Contamination**

The system retrieved primarily from the **Mishnah** (results 1, 2, 5) rather than the **Tanakh** (results 3, 4), despite the query explicitly requesting Tanakh sources. Results 3–4 are tangentially relevant only through metaphor (Jeremiah's "pen of iron," Proverbs on wisdom), suggesting the system relied on vocabulary matching ("ethics," "understanding") rather than semantic coherence. The Mishnah results on blessings and Torah study are entirely disconnected from the query's intent.

**Pattern**: This reveals a **cross-corpus gap** — when a Tanakh-specific query cannot be answered within the Tanakh, the dense retrieval model

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.0000 | 0.0000 |
| Dense (text-dedup) | 0.0000 | 0.0000 |
| Dense Two-Level | 0.0000 | 0.0000 |
| Dense+MMR | 0.0000 | 0.0000 |
| Hybrid RRF | 0.0000 | 0.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**0**] *Berakhot* (Berakhot 9:5) [Mishnah] — One must bless the L-rd for the ill just as he does for the good. [When he blesses "dayan ha'emeth" for the ill, he must…
- [**0**] *Pirkei Avot* (Pirkei Avot 1:1) [Mishnah] — Moses received Torah from Sinai. [I say that because this tractate is not founded on any explanation of a mitzvah of the…
- [**0**] *Proverbs* (Proverbs 30) [Writings] — The words of Agur the son of Jakeh; the burden. The man saith unto Ithiel, unto Ithiel and Ucal: Surely I am brutish, un…
- [**0**] *Jeremiah* (Jeremiah 17) [Prophets] — The sin of Judah is written With a pen of iron, and with the point of a diamond; It is graven upon the tablet of their h…
- [**0**] *Pirkei Avot* (Pirkei Avot 1:15) [Mishnah] — Beth Shammai say: Make your Torah primary [i.e., let your principal endeavor, day and night, be in Torah. And when you t…

#### real-neg-02: What Buddhist and Islamic teachings about the nature of God appear in the Tanakh or Mishnah? *(negative / out-of-scope)*
*Category: negative*  |  **Analysis**: **Analysis:**

The system failed to answer the query. The fundamental error is a **topic absent from corpus**: Buddhist and Islamic theological content does not exist in Jewish texts (Tanakh or Mishnah), making this an impossible retrieval task. The system appears to have attempted semantic matching on partial query terms—"teachings about the nature of God"—and retrieved general Jewish theological passages (blessings, divine justice in Job, monotheistic polemic against idolatry in Isaiah) that match the God-concept portion but completely miss the cross-religious comparative requirement.

A secondary issue is **cross-corpus gap**: the results weakly span both Tanakh (Isaiah, Job) and Mishnah (Pirkei Avot, Berakhot, Chagigah), suggesting the system understood the corpus boundaries but failed to recognize that no single document satisfies the query's comparative intent. The retrieval demonstrates vocabulary matching on isolated terms ("God," "teachings") without semantic understanding that the query asks for content that fundamentally does not exist in the indexed material.

| System | Chunk nDCG@5 | Source nDCG@5 |
|---|--:|--:|
| Dense | 0.0000 | 0.0000 |
| Dense (text-dedup) | 0.0000 | 0.0000 |
| Dense Two-Level | 0.0000 | 0.0000 |
| Dense+MMR | 0.0000 | 0.0000 |
| Hybrid RRF | 0.0000 | 0.0000 |
| FTS-only | 0.0000 | 0.0000 |

Top-5 Dense candidates:

- [**0**] *Pirkei Avot* (Pirkei Avot 1:1) [Mishnah] — Moses received Torah from Sinai. [I say that because this tractate is not founded on any explanation of a mitzvah of the…
- [**0**] *Berakhot* (Berakhot 9:5) [Mishnah] — One must bless the L-rd for the ill just as he does for the good. [When he blesses "dayan ha'emeth" for the ill, he must…
- [**0**] *Chagigah* (Chagigah 2:1) [Mishnah] — Illicit relations are not expounded to three [men at the same time. Recondite matters of Torah are not expounded to them…
- [**0**] *Isaiah* (Isaiah 46) [Prophets] — Bel boweth down, Nebo stoopeth; their idols are upon the beasts, and upon the cattle; the things that ye carried about a…
- [**0**] *Job* (Job 36) [Writings] — Elihu also proceeded, and said: Suffer me a little, and I will tell thee; For there are yet words on God’s behalf. I wil…
