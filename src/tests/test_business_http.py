"""Fictional mixed-source degradation; preserve evidence and source boundaries."""
from src.tests.http_fixtures import SERVICE_HEADERS, http_service_environment
from src.tests.test_answering import sample
from src.tests.test_type_b import service
from src.api.app import create_app
from fastapi.testclient import TestClient


def test_mixed_answer_keeps_policy_when_hrm_is_unavailable(sample):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        owner.answers.catalog, owner.answers.lexicon = sample.catalog, sample.lexicon
        owner.answers.available = True
        owner.context.available = False
        owner.retrieval.read_evidence.return_value = sample.result
        response = client.post('/api/v1/answer', json={'text': 'Chức danh của tôi là gì và chính sách sinh con bao nhiêu tiền?',
                                                       'hrm_user_id': 7})
        assert response.status_code == 200
        value = response.json()
        assert value['status'] == 'partial' and value['claims']
        assert value['employee_evidence'] == dict(status='unavailable',facts=[],source=None,reason='employee_source_unavailable')
        assert not value['employee_claims']
        assert value['sources'] and value['limitations']
        pure = client.post('/api/v1/answer', json={'text': 'Chức danh của tôi là gì?', 'hrm_user_id': 7})
        assert pure.status_code == 503 and pure.json()['error']['code'] == 'employee_source_unavailable'
    employee.find_by_user_id.assert_not_awaited()
