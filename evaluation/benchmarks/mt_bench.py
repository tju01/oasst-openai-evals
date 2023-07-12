import os
import json

from evaluation.utils import replace_model_name_slashes, create_model, compute_model_replies

def get_temperature(category):
    return ({
        'writing': 0.7,
        'roleplay': 0.7,
        'extraction': 0.0,
        'math': 0.0,
        'coding': 0.0,
        'reasoning': 0.0,
        'stem': 0.1,
        'humanities': 0.1,
    })[category]

def generate_assistant_replies(model_type, model_name):
    answers_filepath = os.path.join('reports', 'mt-bench', replace_model_name_slashes(model_name), 'answers.json')
    if os.path.exists(answers_filepath):
        return

    model = create_model(model_type, model_name)

    with open('data/mt-bench/questions.json') as f:
        questions = json.load(f)

    # TODO Actually use those temperatures
    for question in questions.values():
        question['temperature'] = get_temperature(question['category'])

    questions_items = questions.items()

    first_turn_conversations = [[('user', question['turns'][0])] for _question_id, question in questions_items]
    first_turn_replies = compute_model_replies(model, first_turn_conversations)
    first_turn_replies = { question_id: first_turn_replies[i] for i, (question_id, _question) in enumerate(questions_items) }

    second_turn_conversations = [[
        ('user', question['turns'][0]),
        ('assistant', first_turn_replies[question_id]),
        ('user', question['turns'][1]),
    ] for question_id, question in questions_items]

    second_turn_replies = compute_model_replies(model, second_turn_conversations)
    second_turn_replies = { question_id: second_turn_replies[i] for i, (question_id, _question) in enumerate(questions_items) }

    all_replies = { question_id: [first_turn_replies[question_id], second_turn_replies[question_id]] for question_id in questions.keys() }

    os.makedirs(os.path.dirname(answers_filepath), exist_ok=True)
    with open(answers_filepath, 'w') as f:
        json.dump(all_replies, f, indent=4)

def create_judge_conversation(questions, answers, judge_prompt_templates, turn_number, question_id):
    question = questions[question_id]
    answer = answers[question_id]

    if turn_number == 0 and 'reference' not in question:
        prompt_template_name = 'single-v1'
    elif turn_number == 0 and 'reference' in question:
        prompt_template_name = 'single-math-v1'
    elif turn_number == 1 and 'reference' not in question:
        prompt_template_name = 'single-v1-multi-turn'
    elif turn_number == 1 and 'reference' in question:
        prompt_template_name = 'single-math-v1-multi-turn'

    prompt_template = judge_prompt_templates[prompt_template_name]['prompt_template']
    system_prompt = judge_prompt_templates[prompt_template_name]['system_prompt']

    kwargs = {}
    if 'reference' in question:
        kwargs['ref_answer_1'] = question['reference'][0]
        kwargs['ref_answer_2'] = question['reference'][1]

    if turn_number == 0:
        prompt = prompt_template.format(
            question=question['turns'][0],
            answer=answer[0],
            **kwargs,
        )
    else:
        prompt = prompt_template.format(
            question_1=question['turns'][0],
            question_2=question['turns'][1],
            answer_1=answer[0],
            answer_2=answer[1],
            **kwargs,
        )

    return [
        ('system', system_prompt),
        ('user', prompt),
    ]

def compute_judge_replies(model_name):
    judge_replies_filepath = os.path.join('reports', 'mt-bench', replace_model_name_slashes(model_name), 'judge-replies.json')
    if os.path.exists(judge_replies_filepath):
        return

    with open('data/mt-bench/questions.json') as f:
        questions = json.load(f)
    with open('data/mt-bench/judge_prompts.json') as f:
        judge_prompt_templates = json.load(f)
    with open(os.path.join('reports/mt-bench', replace_model_name_slashes(model_name), 'answers.json')) as f:
        answers = json.load(f)

    judge_conversations = [{
        'question_id': question_id,
        'turn_number': turn_number,
        'conversation': create_judge_conversation(questions, answers, judge_prompt_templates, turn_number, question_id),
    } for turn_number in [0, 1] for question_id in questions.keys()]

    # TODO: Only for testing. Replace with gpt-4 later.
    # TODO: Maybe increase the maximum number of output tokens?
    judge_model = create_model('openai', 'gpt-3.5-turbo-0613')

    judge_replies = compute_model_replies(judge_model, [item['conversation'] for item in judge_conversations])
    judge_replies = [{
        'question_id': judge_conversations[i]['question_id'],
        'turn_number': judge_conversations[i]['turn_number'],
        'judge_reply': judge_reply,
    } for i, judge_reply in enumerate(judge_replies)]

    os.makedirs(os.path.dirname(judge_replies_filepath), exist_ok=True)
    with open(judge_replies_filepath, 'w') as f:
        json.dump(judge_replies, f, indent=4)

def evaluate_models(models):
    for model_type, model_name in models:
        generate_assistant_replies(model_type, model_name)
        compute_judge_replies(model_name)
