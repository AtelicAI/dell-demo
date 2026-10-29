"""Optional official TypeSafe decisions. Suggestions are never engineering truth."""
import base64
import hashlib
import io
import json
import math
import os
import re
import time

import httpx

ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
MODEL = 'jev-1.14.0'
VISIBILITY_QUESTIONS = {
    'visibility': {
        'type': 'choice',
        'instructions': ('Classify visible plume-like structure in the infrared image. '
                         'Bright solid equipment is not plume. A still cannot establish motion, '
                         'methane identity, release timing, concentration or leak absence.'),
        'criteria': {
            'none_visible': 'No discernible plume-like structure; this does not establish zero release.',
            'faint_or_ambiguous': 'Faint wisps, ambiguous contrast or equipment that needs temporal review.',
            'clearly_visible': 'Distinct cloud-like or wispy plume structure is visible.'}},
    'equipment_confusion': {
        'type': 'noul',
        'instructions': 'Does bright solid equipment make the presence or boundary of plume ambiguous in this still?'}
}
TEXT_QUESTIONS = {
    'record_type': {
        'type': 'choice', 'instructions': 'Classify this engineering record by its content.',
        'criteria': {'maintenance': 'A maintenance/work-order record or finding',
                     'procedure': 'Instructions or operating procedure',
                     'measurement': 'Recorded sensor observations',
                     'other': 'None of these'}},
    'mentions_completed_repair': {
        'type': 'noul', 'instructions': 'Does the record explicitly state that a repair was completed?'}
}


class JevError(RuntimeError):
    """Sanitized provider error; no secret, raw HTTP body or request data."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def prepare_image(png_bytes):
    """Losslessly pad small native frames to TypeSafe's documented 280px minimum.

    Never overwrite source pixels or interpolate their boundaries. This derived upload
    is not the master used for segmentation labels, evaluation or SAM model inputs.
    """
    from PIL import Image, UnidentifiedImageError
    if len(png_bytes) > 2_000_000 or not png_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        raise JevError('Expected a PNG image no larger than 2MB')
    try:
        with Image.open(io.BytesIO(png_bytes)) as original:
            w, h = original.size
            if not 1 <= w <= 1568 or not 1 <= h <= 1568 or getattr(original, 'n_frames', 1) != 1:
                raise JevError('Expected a still image no larger than 1568px per side')
            image = original.convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError):
        raise JevError('Invalid PNG image') from None
    out_w, out_h = max(280, w), max(280, h)
    left, top = (out_w-w)//2, (out_h-h)//2
    if (out_w, out_h) != (w, h):
        padded = Image.new('RGB', (out_w, out_h), (0, 0, 0))
        padded.paste(image, (left, top))
        image = padded
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    upload = buffer.getvalue()
    return upload, dict(method='rgb_reencode_and_black_pad_to_280_minimum',
                        original_size=[w, h], upload_size=[out_w, out_h],
                        original_content_box=[left, top, left+w, top+h],
                        original_sha256=hashlib.sha256(png_bytes).hexdigest(),
                        upload_sha256=hashlib.sha256(upload).hexdigest(),
                        source_pixels_resampled=False)


def image_request(png_bytes, model):
    upload, preparation = prepare_image(png_bytes)
    ref = 'ogi-' + preparation['upload_sha256']
    state = {'image': ref, 'context': 'Original grayscale optical gas imaging footage, provided as RGB PNG. '
             'Judge visible structure inside original_content_box only; ignore the black padding. '
             'No temporal sequence, radiometry or gas identity is available.',
             'original_content_box': preparation['original_content_box']}
    assets = {ref: dict(type='image', format='png', data_base64=base64.b64encode(upload).decode())}
    return dict(model=model, state=state, questions=VISIBILITY_QUESTIONS, assets=assets), preparation


def _probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise JevError('Invalid decision probability')
    return float(value)


def validate_answers(payload, questions):
    """Validate the actual API contract even though the provider promises typed output."""
    if not isinstance(payload, dict) or not isinstance(payload.get('model'), str) or not payload['model']:
        raise JevError('Missing resolved model identifier')
    answers = payload.get('answers')
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevError('Decision questions and answers do not match')
    cleaned = {}
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            raise JevError('Invalid decision type')
        if question['type'] == 'choice':
            options = question['criteria']
            probabilities = answer.get('probabilities')
            if answer.get('choice') not in options or not isinstance(probabilities, dict) or set(probabilities) != set(options):
                raise JevError('Invalid decision choices')
            probabilities = {k: _probability(v) for k, v in probabilities.items()}
            if not math.isclose(sum(probabilities.values()), 1., abs_tol=.02):
                raise JevError('Invalid decision distribution')
            cleaned[name] = dict(type='choice', choice=answer['choice'],
                                 confidence=_probability(answer.get('confidence')), probabilities=probabilities)
        elif question['type'] == 'noul':
            cleaned[name] = dict(type='noul', noul=_probability(answer.get('noul')))
        else:
            raise JevError('Unsupported decision question type')
    return cleaned


class JevClient:
    def __init__(self, api_key=None, model=MODEL, transport=None):
        self.api_key = api_key if api_key is not None else os.getenv('TYPESAFE_API_KEY', '')
        if not self.api_key or not self.api_key.strip():
            raise JevError('TYPESAFE_API_KEY is not configured')
        if not isinstance(model, str) or re.fullmatch(r'jev-\d+\.\d+\.\d+', model) is None:
            raise JevError('Use a pinned TypeSafe model version, such as jev-1.14.0')
        self.model, self.transport = model, transport

    def decide(self, state, questions, assets=None):
        if not questions or any(q.get('type') not in {'choice', 'noul'} for q in questions.values()):
            raise JevError('Unsupported or empty decision questions')
        body = dict(model=self.model, state=state, questions=questions)
        headers = {'Authorization': 'Bearer ' + self.api_key}
        if assets:
            body['assets'] = assets
            headers['TypeSafe-Danger-Zone-Images'] = 'true'
        started = time.perf_counter()
        try:
            # Fixed official host, no redirects or proxy environment credential forwarding.
            # No automatic retries: a timeout may already have consumed a paid inference.
            with httpx.Client(timeout=30., follow_redirects=False, trust_env=False, transport=self.transport) as client:
                response = client.post(ENDPOINT, headers=headers, json=body)
        except httpx.HTTPError:
            raise JevError('TypeSafe transport unavailable; decision not accepted') from None
        if response.status_code != 200:
            raise JevError(f'TypeSafe HTTP {response.status_code}; decision not accepted')
        try:
            payload = response.json()
        except ValueError:
            raise JevError('Invalid TypeSafe JSON response') from None
        answers = validate_answers(payload, questions)
        if payload['model'] != self.model:
            raise JevError('Provider did not resolve the requested pinned model')
        usage = payload.get('usage', {})
        usage = {k: v for k, v in usage.items() if k in {'input_tokens', 'output_tokens'}
                 and isinstance(v, int) and not isinstance(v, bool) and v >= 0} if isinstance(usage, dict) else {}
        return dict(provider='typesafe', endpoint=ENDPOINT, requested_model=self.model,
                    resolved_model=payload['model'], answers=answers, usage=usage,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                    request_sha256=digest(body), origin_type='model_suggestion',
                    calibrated_on_ogi=False, human_review_required=True, ground_truth=False)

    def classify_text(self, text):
        if not isinstance(text, str) or not 0 < len(text) <= 16000:
            raise JevError('Text must contain 1–16000 characters')
        return self.decide(text, TEXT_QUESTIONS)

    def triage_image(self, png_bytes):
        # Stable image reference supports exact-request caching; no workbook label/clip name
        # is sent as context, so it cannot bias the visual judgment.
        body, preparation = image_request(png_bytes, self.model)
        result = self.decide(body['state'], body['questions'], body['assets'])
        return {**result, 'image_preparation': preparation}

    def image_cache_key(self, png_bytes):
        return digest(image_request(png_bytes, self.model)[0])


def review_priority(result):
    """Order human review only; never accept labels or authorize equipment actions."""
    visibility = result['answers']['visibility']
    confusion = result['answers']['equipment_confusion']['noul']
    return ('high' if visibility['choice'] == 'faint_or_ambiguous' or
            visibility['confidence'] < .8 or confusion >= .5 else 'normal')
