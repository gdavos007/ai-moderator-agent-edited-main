"""Async LLM response analysis.

Depends on ``openai`` (NOT livekit) for API calls.
"""
import logging
from dataclasses import dataclass

import openai as openai_client

logger = logging.getLogger(__name__)


@dataclass
class ResponseAnalysis:
    """Result of unified response analysis via LLM"""
    is_relevant: bool
    is_already_answered_claim: bool
    is_repeat_request: bool
    partial_repeat_status: str  # "NO_REPEAT", "REPEAT_ONLY", or "PARTIAL"
    partial_answer: str
    unanswered_questions: str


async def analyze_response(question_text: str, response_text: str, survey_description: str = "") -> ResponseAnalysis:
    """
    Unified LLM analysis of participant response. Checks multiple aspects in a single call:
    1. Is the response relevant to the question?
    2. Is the participant claiming they already answered?
    3. Is there a partial answer with a repeat request for remaining parts?

    Args:
        question_text: The question that was asked
        response_text: The participant's response
        survey_description: Description of the survey topic (from meta.description)

    Returns:
        ResponseAnalysis with all detection results
    """
    default_result = ResponseAnalysis(
        is_relevant=True,
        is_already_answered_claim=False,
        is_repeat_request=False,
        partial_repeat_status="NO_REPEAT",
        partial_answer="",
        unanswered_questions=""
    )

    if not question_text or not response_text:
        return default_result

    survey_context = f"Survey topic: {survey_description}" if survey_description else "Survey topic: General survey"

    try:
        system_prompt = f"""You are a survey response analyzer. Analyze the participant's response for FOUR aspects.

{survey_context}

1. RELEVANCE: Is the response relevant to the question asked?
   Mark as "relevant" if:
   - The response attempts to answer the question, even if brief or incomplete
   - The response mentions topics related to the survey subject described above
   - UNCERTAINTY expressions: "I don't know", "I'm not sure", etc. (these ARE valid responses)
   - AFFIRMATIVE/NEGATIVE responses: "yes", "no", "definitely", etc.
   - The response shows engagement with the topic even if indirect

   Mark as "not_relevant" if:
   - The response is about a completely unrelated topic (weather, sports, random subjects unrelated to survey)
   - The response is hostile/rude and deflects instead of answering (e.g., "Why are you asking me this?", "That's a stupid question")
   - The response ignores the question entirely with irrelevant content
   - Examples: "Weather is nice today", "I had pizza for lunch", "Mind your own business"

2. ALREADY_ANSWERED: Is the participant ONLY claiming they already answered WITHOUT providing an answer?
   - "claim" ONLY if they say "I already answered", "I told you before", etc. AND do NOT provide any actual answer content
   - "no_claim" if they provide an actual answer (even if they ALSO mention "I already answered")

   CRITICAL: If the response contains BOTH a claim AND an actual answer, mark as "no_claim"!
   - "Life changing. I've answered this." = "no_claim" (contains answer "life changing")
   - "I already told you, it's the single point of contact." = "no_claim" (contains answer)
   - "I already answered that question." = "claim" (no actual answer provided)
   - "Same as what I said before." = "claim" (no specific content, just reference)

3. REPEAT_REQUEST: Is the participant asking to repeat/hear the question again?
   - "repeat" if they're asking to hear the question again: "can you repeat that", "what was the question", "I didn't hear you", "say that again", "come again", "pardon", "sorry what"
   - "no_repeat" if they're providing an answer (even if the answer mentions "didn't hear")

   CRITICAL DISTINCTION:
   - "I didn't hear you" or "I didn't catch that" = repeat request (asking to repeat question)
   - "I didn't hear about [topic]" or "I never heard of that" = NOT a repeat request (this is an ANSWER about not having heard of something)

4. PARTIAL_REPEAT: Did they provide a PARTIAL answer AND EXPLICITLY ask to repeat/hear the remaining parts?
   - "NO_REPEAT" - Normal response, even if incomplete (DEFAULT - use this most of the time)
   - "REPEAT_ONLY" - Just asking to repeat the full question, no answer given
   - "PARTIAL" - ONLY use when BOTH conditions are met:
     a) They gave an answer to SOME parts of a multi-part question
     b) They EXPLICITLY asked to hear the rest: "what else?", "what was the other part?", "repeat the rest", "what's the other question?"

   CRITICAL: DO NOT use PARTIAL just because the answer seems incomplete!
   - If they answered some parts but didn't ask for remaining parts -> use NO_REPEAT
   - If they asked "what else?" or "what was the other question?" -> use PARTIAL

   WHEN PARTIAL: The unanswered_questions field must contain ONLY the sub-question(s) they DID NOT answer.
   - CAREFULLY compare each part of the question against what they said
   - If they mentioned a topic, that part IS answered (even if brief)
   - Example: Question "Where do you live AND what do you do for work?"
     Response "I'm an engineer in Dallas. What else?"
     - They answered BOTH parts (engineer=work, Dallas=where)
     - unanswered_questions should be EMPTY because both were answered
     - This should be NO_REPEAT, not PARTIAL!

   - Example: Question "Where do you live AND what do you do for work?"
     Response "I live in Dallas. What was the other part?"
     - They answered WHERE (Dallas) but NOT work
     - unanswered_questions: "what do you do for work"

RESPONSE FORMAT (use | as delimiter, exactly 6 fields):
<relevance>|<already_answered>|<repeat_request>|<partial_status>|<partial_answer>|<unanswered_questions>

Examples:
- Normal complete answer: "relevant|no_claim|no_repeat|NO_REPEAT||"
- Incomplete answer (no repeat request): "relevant|no_claim|no_repeat|NO_REPEAT||" (NOT PARTIAL!)
- Asking to repeat full question: "relevant|no_claim|repeat|NO_REPEAT||"
- "I didn't hear about [topic] before": "relevant|no_claim|no_repeat|NO_REPEAT||" (THIS IS AN ANSWER!)
- Off-topic like "Weather is nice": "not_relevant|no_claim|no_repeat|NO_REPEAT||"
- Uncertainty "I don't know": "relevant|no_claim|no_repeat|NO_REPEAT||"
- Already answered claim ONLY (no answer): "relevant|claim|no_repeat|NO_REPEAT||"
- Claim WITH answer "Life changing. I've answered this.": "relevant|no_claim|no_repeat|NO_REPEAT||" (has actual answer!)
- Partial with explicit request: Question "What do you like AND dislike?", Response "I like the food. What else did you ask?": "relevant|no_claim|no_repeat|PARTIAL|I like the food.|what do you dislike"
- Answered both parts: Question "Where do you live AND work?", Response "Dallas, I'm retired. What else?": "relevant|no_claim|no_repeat|NO_REPEAT||" (Both answered, no unanswered parts!)

IMPORTANT: Default to NO_REPEAT. Only use PARTIAL when user EXPLICITLY asks for remaining parts AND there actually ARE unanswered parts.

5. META-COMMENTARY: If the participant is commenting on the survey PROCESS rather than answering the question, do NOT treat it as a repeat request, partial answer, or already-answered claim.
   Examples of meta-commentary (classify as relevant, no_claim, no_repeat, NO_REPEAT):
   - "You skipped the last part" / "You're skipping on that question" -- complaint about delivery, NOT a request to repeat
   - "I answered the question" / "I already said that" -- frustration, NOT a genuine already-answered claim (use no_claim)
   - "You didn't hear me" / "Why are you asking me again" -- frustration, NOT a repeat request
   - "That's the same question as before" -- observation, NOT a repeat request
   These responses may contain substantive content mixed with complaints. Extract and evaluate the substantive content."""

        user_prompt = f"""Question: {question_text}

Participant's Response: {response_text}

Analyze this response and output in the exact format: relevance|already_answered|repeat_request|partial_status|partial_answer|unanswered_questions"""

        client = openai_client.AsyncOpenAI()
        llm_response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=500,
            temperature=0.0
        )

        result = llm_response.choices[0].message.content.strip()
        logger.info(f"Response analysis - Question: '{question_text[:50]}...', Response: '{response_text[:50]}...', LLM says: '{result}'")

        # Parse the response (6 fields separated by |)
        parts = result.split("|")
        if len(parts) >= 6:
            relevance = parts[0].strip().lower()
            already_answered = parts[1].strip().lower()
            repeat_request = parts[2].strip().lower()
            partial_status = parts[3].strip().upper()
            partial_answer = parts[4].strip()
            unanswered = parts[5].strip()

            is_relevant = "not_relevant" not in relevance and "not relevant" not in relevance
            is_claim = "claim" in already_answered and "no_claim" not in already_answered
            is_repeat = "repeat" in repeat_request and "no_repeat" not in repeat_request

            if partial_status not in ("NO_REPEAT", "REPEAT_ONLY", "PARTIAL"):
                partial_status = "NO_REPEAT"

            analysis = ResponseAnalysis(
                is_relevant=is_relevant,
                is_already_answered_claim=is_claim,
                is_repeat_request=is_repeat,
                partial_repeat_status=partial_status,
                partial_answer=partial_answer if partial_status == "PARTIAL" else "",
                unanswered_questions=unanswered if partial_status in ("REPEAT_ONLY", "PARTIAL") else ""
            )

            # Post-LLM sanity checks
            if partial_status == "PARTIAL" and unanswered:
                _unanswered_words = len(unanswered.split())
                if _unanswered_words <= 1:
                    logger.info(
                        f"Downgrading weak PARTIAL -- unanswered_questions "
                        f"too vague ({_unanswered_words} word): '{unanswered}'"
                    )
                    analysis = ResponseAnalysis(
                        is_relevant=is_relevant,
                        is_already_answered_claim=is_claim,
                        is_repeat_request=is_repeat,
                        partial_repeat_status="NO_REPEAT",
                        partial_answer="",
                        unanswered_questions="",
                    )

            if not is_relevant:
                logger.warning(f"Off-topic response detected: '{response_text[:100]}'")
            if is_claim:
                logger.warning(f"'Already answered' claim detected: '{response_text[:100]}'")
            if is_repeat:
                logger.info(f"Repeat request detected: '{response_text[:100]}'")
            if partial_status == "PARTIAL":
                logger.info(f"Partial answer detected. Answer: '{partial_answer[:50]}...', Unanswered: '{unanswered[:50]}...'")

            return analysis
        elif len(parts) >= 5:
            # Backward compatibility: handle old 5-field format
            relevance = parts[0].strip().lower()
            already_answered = parts[1].strip().lower()
            partial_status = parts[2].strip().upper()
            partial_answer = parts[3].strip()
            unanswered = parts[4].strip()

            is_relevant = "not_relevant" not in relevance and "not relevant" not in relevance
            is_claim = "claim" in already_answered and "no_claim" not in already_answered

            if partial_status not in ("NO_REPEAT", "REPEAT_ONLY", "PARTIAL"):
                partial_status = "NO_REPEAT"

            return ResponseAnalysis(
                is_relevant=is_relevant,
                is_already_answered_claim=is_claim,
                is_repeat_request=(partial_status == "REPEAT_ONLY"),
                partial_repeat_status=partial_status,
                partial_answer=partial_answer if partial_status == "PARTIAL" else "",
                unanswered_questions=unanswered if partial_status in ("REPEAT_ONLY", "PARTIAL") else ""
            )
        else:
            logger.warning(f"Unexpected LLM response format (expected 6 parts): {result}")
            return default_result

    except Exception as e:
        logger.error(f"Error analyzing response: {e}")
        return default_result


async def check_response_relevance(question_text: str, response_text: str, survey_description: str = "") -> bool:
    """
    Legacy wrapper for analyze_response(). Returns only relevance check result.
    Prefer using analyze_response() directly for full analysis.
    """
    analysis = await analyze_response(question_text, response_text, survey_description)
    return analysis.is_relevant
