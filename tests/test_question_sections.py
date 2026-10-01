"""Regression coverage for independent question-section discovery."""
import io
import zipfile
from html import escape
from unittest.mock import patch

import pytest
from modules.docx_parser import DocxParser
from modules.ai_groq_parser import GroqAnswerResolver
from modules import review_service


def docx(lines):
    body = ''.join(f'<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>' for line in lines)
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return buffer.getvalue()


def question(text, key):
    return [text, 'A. One', 'B. Two', 'C. Three', 'D. Four', f'Answer: {key}']


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('headings', [
    ('Comprehension Questions', 'Vocabulary Quiz'),
    ('## Reading Comprehension', '2. Vocabulary Quiz (10 questions)'),
    ('Part B: Comprehension Questions - Grade 5', 'Section A: Vocabulary'),
])
def test_order_and_heading_formats(reverse, headings):
    comp = [headings[0]] + question('Why exercise?', 'B')
    vocab = [headings[1]] + question('What does active mean?', 'C')
    data = docx(vocab + comp if reverse else comp + vocab)
    comprehension = DocxParser.parse_comprehension_questions(data)
    vocabulary = DocxParser.parse_vocabulary_questions(data)
    assert [q['raw_question'] for q in comprehension] == ['Why exercise?']
    assert [q['answer'] for q in comprehension] == ['B']
    assert [q['raw_question'] for q in vocabulary] == ['What does active mean?']
    assert [q['answer'] for q in vocabulary] == ['C']


def test_repeated_sections_keep_local_keys():
    data = docx(['Vocabulary Quiz'] + question('Word one?', 'C') +
                ['Comprehension Questions'] + question('Why?', 'B') +
                ['Vocabulary Quiz'] + question('Word two?', 'D'))
    assert [q['answer'] for q in DocxParser.parse_vocabulary_questions(data)] == ['C', 'D']
    assert len(DocxParser.parse_comprehension_questions(data)) == 1


def test_question_prose_is_not_heading():
    assert DocxParser.heading_kind('What is a vocabulary quiz?') is None
    assert DocxParser.heading_kind('A. Comprehension Questions') is None
    assert DocxParser.heading_kind('Vocabulary') is None


def test_unlabelled_legacy_and_vocab_only():
    assert len(DocxParser.parse_comprehension_questions(docx(question('Why?', 'A')))) == 1
    assert DocxParser.parse_comprehension_questions(docx(['Vocabulary Quiz'] + question('Word?', 'B'))) == []


def test_review_ai_receives_only_comprehension(tmp_path):
    path = tmp_path / 'First language.docx'
    path.write_bytes(docx(['Vocabulary Quiz'] + question('Word?', 'B') +
                         ['Comprehension Questions', 'Unusual comprehension layout']))
    with patch.object(GroqAnswerResolver, 'is_available', return_value=True), \
         patch.object(GroqAnswerResolver, 'get_api_key', return_value='fake'), \
         patch.object(GroqAnswerResolver, 'extract_all_questions_with_groq', return_value={'success': False}) as extract:
        result = review_service.prepare_story_review(
            {'story_name': 'Test', 'drive_url': 'test', 'docx_path': str(path)},
            {'cache_dir': str(tmp_path)}, download_if_missing=False)
    assert not result['success']
    assert extract.call_args.kwargs['section_text'] == 'Unusual comprehension layout'


def test_empty_section_does_not_fall_back_to_entire_document():
    with patch.object(GroqAnswerResolver, 'get_api_key', return_value='fake'), \
         patch('modules.ai_groq_parser.GROQ_AVAILABLE', True), \
         patch.object(GroqAnswerResolver, 'extract_rich_styled_text') as full_text:
        result = GroqAnswerResolver.extract_all_questions_with_groq(b'', {}, section_text='')
    assert not result['success']
    full_text.assert_not_called()


def test_soft_line_breaks_separate_headings_and_choices():
    data = docx(['placeholder'])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read('word/document.xml').decode()
    text = ['Vocabulary Quiz'] + question('Word?', 'C') + ['Comprehension Questions'] + question('Why?', 'B')
    runs = '<w:br/>'.join(f'<w:r><w:t>{escape(line)}</w:t></w:r>' for line in text)
    xml = xml.replace('<w:r><w:t>placeholder</w:t></w:r>', runs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    assert [q['answer'] for q in DocxParser.parse_comprehension_questions(buffer.getvalue())] == ['B']
    assert [q['answer'] for q in DocxParser.parse_vocabulary_questions(buffer.getvalue())] == ['C']


def test_vocabulary_ai_prompt_excludes_following_comprehension():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.chat.completions.create.return_value.choices[0].message.content = '{"questions": []}'
    data = docx(['Vocabulary Quiz'] + question('Word?', 'C') +
                ['Comprehension Questions'] + question('Why exercise?', 'B'))
    with patch.object(GroqAnswerResolver, 'get_api_key', return_value='fake'), \
         patch('modules.ai_groq_parser.GROQ_AVAILABLE', True), \
         patch('modules.ai_groq_parser.Groq', return_value=client, create=True):
        GroqAnswerResolver.extract_vocab_questions_with_groq(data, {})
    prompt = client.chat.completions.create.call_args.kwargs['messages'][1]['content']
    assert 'Word?' in prompt
    assert 'Why exercise?' not in prompt


@pytest.mark.parametrize("label,kind", [
    ("Comprehention Questions", "comprehension"),
    ("Reading Comprehensoin Questions", "comprehension"),
    ("Vocabulery Quiz", "vocabulary"),
    ("Vocabulary Quizz", "vocabulary"),
    ("## Section B: Vocabulery Questions (10 questions)", "vocabulary"),
    ("Cambridge EFL Vocabulery Quiz", "vocabulary"),
    ("Grade 5 Comprehention Test", "comprehension"),
    ("Part A: Vocabulery", "vocabulary"),
])
def test_heading_typos(label, kind):
    assert DocxParser.heading_kind(label) == kind


@pytest.mark.parametrize("label", [
    "What is a vocabulery quiz?", "A. Vocabulery Quiz", "B) Comprehention Questions",
    "Answer: Vocabulary Quiz", "Vocabulery", "Vocabulary in context",
    "Comprehention Questions are easy", "No Comprehention Questions",
    "Vocabulary Quit", "Comprehensive Questions", "Random words",
])
def test_fuzzy_matching_rejects_prose_and_unrelated_labels(label):
    assert DocxParser.heading_kind(label) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_typo_sections_remain_separate(reverse):
    comp = ["Comprehention Questions"] + question("Why exercise?", "B")
    vocab = ["Vocabulery Quiz"] + question("Word meaning?", "C")
    data = docx(vocab + comp if reverse else comp + vocab)
    assert [q["answer"] for q in DocxParser.parse_comprehension_questions(data)] == ["B"]
    assert [q["answer"] for q in DocxParser.parse_vocabulary_questions(data)] == ["C"]
    assert "Word meaning?" not in DocxParser.question_section_text(data, "comprehension")
    assert "Why exercise?" not in DocxParser.question_section_text(data, "vocabulary")
