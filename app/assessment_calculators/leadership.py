def process_assessment_data(payload: dict) -> dict:
    """
    Processes raw API payload to calculate totals and pass/fail status.
    Ensures backend logic matches the UI requirements.
    """
    scores = payload.get("scores", {})
    
    # 1. Convert score keys to ints if they are strings (common in JSON)
    # and filter out any None values
    clean_scores = {int(k): v for k, v in scores.items() if v is not None}
    
    # 2. Calculate Total Score
    total_score = sum(clean_scores.values())
    
    # 3. Determine Pass/Fail Status
    # Rule A: Must have all 10 questions answered
    # Rule B: Total score must be 30 or above
    # Rule C: Automatic fail if any single score is a 1
    has_a_one = any(v == 1 for v in clean_scores.values())
    all_answered = len(clean_scores) == 10
    
    passed = all_answered and total_score >= 30 and not has_a_one
    
    # 4. Map back to the expected PDF builder format
    return {
        "cadet_name": payload.get("cadet_name", "Unknown"),
        "exercise_no": payload.get("exercise_no", ""),
        "exercise_name": payload.get("exercise_name", ""),
        "scores": clean_scores,
        "total_score": total_score,
        "passed": passed,
        "assessor_name": payload.get("assessor_name", ""),
        "assessor_signature": payload.get("assessor_signature"),
        "date": payload.get("date", ""),
        "debriefing_notes": payload.get("debriefing_notes", ""),
    }