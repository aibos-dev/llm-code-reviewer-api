"""
API Endpoint Definitions
========================

/v1/review, /v1/review/feedback - synchronous
/v1/jobs - async queue
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .database import get_db_session
from .llm_engines.ollama_engine import OllamaEngine
from .schemas import ReviewFeedbackRequest, ReviewRequest, ReviewResponse, ReviewResponseCategory
from .services import cancel_job, generate_and_save_review, get_job_status, queue_review_job, save_feedback

router = APIRouter(prefix="/v1", tags=["reviews"])
logger = logging.getLogger(__name__)


@router.post("/review", response_model=ReviewResponse)
def review_code(review_req: ReviewRequest, db_session: Session = Depends(get_db_session)) -> ReviewResponse:
    """
    Synchronous code review returning multiple categories.
    """
    try:
        llm_engine = OllamaEngine()
        review_obj = generate_and_save_review(
            session=db_session,
            llm_engine=llm_engine,
            language_str=review_req.language,
            sourcecode_str=review_req.sourceCode,
            diff_str=review_req.diff,
            filename_str=review_req.fileName,
            options_dict=review_req.options,
        )

        response_cats = [
            ReviewResponseCategory(category=cat.category_name, message=cat.message) for cat in review_obj.categories
        ]
        return ReviewResponse(reviewId=str(review_obj.review_id), reviews=response_cats)

    except Exception:
        logger.exception("Error occurred while performing code review.")
        raise HTTPException(status_code=500, detail="Failed to perform code review.")


@router.post("/review/feedback")
def review_feedback(review_req: ReviewFeedbackRequest, db_session: Session = Depends(get_db_session)) -> dict:
    """
    Allows users to provide feedback (e.g., Good/Bad) for an existing review.
    """
    try:
        feedback_list = [(f.category, f.feedback) for f in review_req.feedbacks]
        save_feedback(db_session, review_req.reviewId, feedback_list)
        return {"status": "success", "message": "Feedback saved."}

    except HTTPException as e:
        raise e

    except Exception:
        logger.exception("Error occurred while submitting feedback.")
        raise HTTPException(status_code=500, detail="Failed to save feedback.")


# === Asynchronous queue endpoints ===


@router.post("/jobs")
def create_review_job(review_req: ReviewRequest, db_session: Session = Depends(get_db_session)) -> dict:
    """
    Creates a new code review job, processed asynchronously.
    """
    try:
        job_id = queue_review_job(db_session, review_req)
        return {
            "jobId": job_id,
            "status": "queued",
            "message": f"Job accepted. Check status via GET /v1/jobs/{job_id}",
        }
    except Exception:
        logger.exception("Error while creating job.")
        raise HTTPException(status_code=500, detail="Failed to create job.")


@router.get("/jobs/{jobId}")
def get_review_job(jobId: str, db_session: Session = Depends(get_db_session)) -> dict:
    """
    Retrieves job status and results if completed.
    """
    job_info = get_job_status(db_session, jobId)
    if not job_info:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job_info


@router.put("/jobs/{jobId}")
def update_review_job(jobId: str, update_data: dict, db_session: Session = Depends(get_db_session)) -> dict:
    """
    Allows canceling an in-progress job by passing { "status": "canceled" }.
    """
    if update_data.get("status") == "canceled":
        canceled_job = cancel_job(db_session, jobId)
        if not canceled_job:
            raise HTTPException(status_code=409, detail="Job not found or already completed/canceled/error.")
        return canceled_job

    raise HTTPException(status_code=400, detail="Unsupported update request.")
