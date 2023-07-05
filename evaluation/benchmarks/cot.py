import re
import os
import json

import datasets
import tqdm

from evaluation.utils import create_model, replace_model_name_slashes, compute_model_replies

def create_conversation(answer_format, question):
    return [
        ('user',
            'Please answer the following question step-by-step. '
            'Do not output the answer immediately. '
            'Instead first explain your reasoning step-by-step. '
            'Only afterwards output the answer. '
            'The final line should contain the answer ' + answer_format + ' without anything else.'
            '\n\n'
            + question),
    ]

def evaluate_model_on_dataset(*, name, model, data, question_column, answer_column, answer_format, is_correct,
        output_path, limit=float('inf'), create_question=None):
    output_file_path = os.path.join(output_path, name + '.json')
    if os.path.exists(output_file_path):
        with open(output_file_path) as f:
            return json.load(f)['score']

    print('Evaluating model on ', name)

    num_total = 0
    requests = []
    for item in data.select(range(min(limit, len(data)))):
        if isinstance(question_column, str):
            question = item[question_column]
        elif isinstance(question_column, list):
            question = create_question({ column: item[column] for column in question_column })
        correct_answer = item[answer_column]
        conversation = create_conversation(answer_format, question)
        requests.append({ 'id': num_total, 'question': question, 'correct_answer': correct_answer,
            'conversation': conversation })
        num_total += 1

    model_answers = compute_model_replies(model, [request['conversation'] for request in requests])

    model_outputs = []
    num_correct = 0
    for i, request in enumerate(requests):
        model_answer = model_answers[i]
        model_answer_is_correct = is_correct(model_answer=model_answer.split('\n')[-1], correct_answer=request['correct_answer'])
        model_outputs.append({ 'id': request['id'], 'question': request['question'], 'correct_answer': request['correct_answer'],
            'model_answer': model_answer, 'correct': model_answer_is_correct })
        if model_answer_is_correct:
            num_correct += 1

    score = num_correct / num_total

    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w') as f:
        json.dump({
            'score': score,
            'model_outputs': model_outputs,
        }, f, indent=4)

    return score

def evaluate_model_on_gsm8k(model, output_path):
    def is_correct(model_answer, correct_answer):
        model_answer_matches = re.findall(r'(\d+(,\d+)*(\.\d+)?)', model_answer)
        if len(model_answer_matches) == 0:
            return False
        model_answer_processed = float(model_answer_matches[-1][0].replace(',', ''))
        correct_answer_processed = float(correct_answer.split('\n')[-1].split('####')[1].strip())
        return abs(model_answer_processed - correct_answer_processed) < 1e-8

    return evaluate_model_on_dataset(
        name='gsm8k',
        model=model,
        data=datasets.load_dataset('gsm8k', 'main')['test'],
        question_column='question',
        answer_column='answer',
        answer_format='as a single number',
        is_correct=is_correct,
        output_path=output_path,
        limit=100,
    )

def evaluate_model_on_bbh(model, output_path):
    def is_correct(model_answer, correct_answer):
        model_answer_matches = re.findall(r'\([ABCDEFGHIJKLMNOPQRSTUVWXYZ]\)', model_answer)
        if len(model_answer_matches) == 0:
            return False
        return model_answer_matches[-1] == correct_answer

    tasks = [
        'date_understanding',
        'disambiguation_qa',
        'geometric_shapes',
        'hyperbaton',
        'logical_deduction_five_objects',
        'logical_deduction_seven_objects',
        'logical_deduction_three_objects',
        'movie_recommendation',
        'penguins_in_a_table',
        'reasoning_about_colored_objects',
        'ruin_names',
        'salient_translation_error_detection',
        'snarks',
        'temporal_sequences',
        'tracking_shuffled_objects_five_objects',
        'tracking_shuffled_objects_seven_objects',
        'tracking_shuffled_objects_three_objects',
    ]

    accuracies = {
        task: evaluate_model_on_dataset(
            name='bbh/' + task,
            model=model,
            data=datasets.load_dataset('lukaemon/bbh', task)['test'],
            question_column='input',
            answer_column='target',
            answer_format='as a single letter with parenthesis',
            is_correct=is_correct,
            output_path=output_path,
            limit=20,
        ) for task in tasks
    }

    return {
        'tasks': accuracies
    }

def evaluate_model_on_mmlu(model, output_path):
    # Can't we somehow get that from `datasets` directly? Haven't found a way...
    tasks = ['abstract_algebra', 'anatomy', 'astronomy', 'business_ethics', 'clinical_knowledge', 'college_biology', 'college_chemistry',
        'college_computer_science', 'college_mathematics', 'college_medicine', 'college_physics', 'computer_security', 'conceptual_physics',
        'econometrics', 'electrical_engineering', 'elementary_mathematics', 'formal_logic', 'global_facts', 'high_school_biology', 'high_school_chemistry',
        'high_school_computer_science', 'high_school_european_history', 'high_school_geography', 'high_school_government_and_politics',
        'high_school_macroeconomics', 'high_school_mathematics', 'high_school_microeconomics', 'high_school_physics', 'high_school_psychology',
        'high_school_statistics', 'high_school_us_history', 'high_school_world_history', 'human_aging', 'human_sexuality', 'international_law',
        'jurisprudence', 'logical_fallacies', 'machine_learning', 'management', 'marketing', 'medical_genetics', 'miscellaneous', 'moral_disputes',
        'moral_scenarios', 'nutrition', 'philosophy', 'prehistory', 'professional_accounting', 'professional_law', 'professional_medicine',
        'professional_psychology', 'public_relations', 'security_studies', 'sociology', 'us_foreign_policy', 'virology', 'world_religions']

    def create_question(columns):
        return columns['question'] + '\n\n' + '\n'.join(['(' + name + ') ' + columns['choices'][index] for index, name in enumerate(['A', 'B', 'C', 'D'])])

    def is_correct(model_answer, correct_answer):
        model_answer_matches = re.findall(r'\([ABCD]\)', model_answer)
        if len(model_answer_matches) == 0:
            return False
        return model_answer_matches[-1] == '(' + ['A', 'B', 'C', 'D'][correct_answer] + ')'

    accuracies = {
        task: evaluate_model_on_dataset(
            name='mmlu/' + task,
            model=model,
            data=datasets.load_dataset('cais/mmlu', task)['test'],
            question_column=['question', 'choices'],
            create_question=create_question,
            answer_column='answer',
            answer_format='as a single letter with parenthesis',
            is_correct=is_correct,
            output_path=output_path,
            limit=10,
        ) for task in tasks
    }

    return {
        'tasks': accuracies
    }

def evaluate_model(model_type, model_name):
    output_folder = os.path.join('reports', 'cot', replace_model_name_slashes(model_name))
    final_scores_file = os.path.join(output_folder, 'scores.json')
    if os.path.exists(final_scores_file):
        return

    model = create_model(model_type, model_name, max_new_tokens=1024)

    tasks_path = os.path.join(output_folder, 'tasks')

    gsm8k_score = evaluate_model_on_gsm8k(model, tasks_path)
    bbh_scores = evaluate_model_on_bbh(model, tasks_path)
    mmlu_scores = evaluate_model_on_mmlu(model, tasks_path)

    output = {
        'gsm8k': gsm8k_score,
        'bbh': bbh_scores,
        'mmlu': mmlu_scores,
    }

    os.makedirs(os.path.dirname(final_scores_file), exist_ok=True)
    with open(final_scores_file, 'w') as f:
        json.dump(output, f, indent=4)

def evaluate_models(models):
    for model_type, model_name in models:
        evaluate_model(model_type, model_name)
