import os
import subprocess
import urllib.request
import zipfile
import json
import urllib.request
import tarfile
import shutil

from evaluation.benchmarks.utils import model_name_to_filename
from evaluation.models.models import create_model, compute_model_replies

def install_ds1000(cwd):
    installation_done_file = os.path.join(cwd, 'install-ds1000-done')
    if os.path.exists(installation_done_file):
        return

    python_output_directory = os.path.join(cwd, 'python3.8.16')

    urllib.request.urlretrieve(
        'https://github.com/indygreg/python-build-standalone/releases/download/20230726/cpython-3.8.16+20230726-x86_64-unknown-linux-gnu-install_only.tar.gz',
        python_output_directory + '.tar.gz'
    )

    tar = tarfile.open(python_output_directory + '.tar.gz')
    tar.extractall(python_output_directory)

    python_path = os.path.join(python_output_directory, 'python', 'bin', 'python3.8')

    if not os.path.exists(os.path.join(cwd, 'venv')):
        subprocess.run([python_path, '-m', 'venv', 'venv'], cwd=cwd)

    if not os.path.exists(os.path.join(cwd, 'DS-1000')):
        subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/HKUNLP/DS-1000.git'], cwd=cwd)

    new_environment = os.environ.copy()
    new_environment['PATH'] = os.path.join(cwd, 'venv/bin') + ':' + os.environ['PATH']

    pip_tmpdir = os.path.join(cwd, 'pip-tmpdir')
    new_environment['TMPDIR'] = pip_tmpdir
    os.makedirs(pip_tmpdir, exist_ok=True)

    new_environment['CUDA_VISIBLE_DEVICES'] = '-1'

    cwd = os.path.join(cwd, 'DS-1000')
    subprocess.run(['pip', 'install', '-r', 'requirements.txt'], cwd=cwd, env=new_environment)

    os.close(os.open(installation_done_file, os.O_CREAT))

def download_ds1000_data(tmpdir):
    url = 'https://github.com/HKUNLP/DS-1000/raw/main/ds1000_data.zip'
    zip_filepath = os.path.join(tmpdir, 'ds1000_data.zip')
    if not os.path.exists(zip_filepath):
        urllib.request.urlretrieve(url, zip_filepath)

    output_dir = os.path.join(tmpdir, 'ds1000_data')
    if not os.path.exists(output_dir):
        with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
            zip_ref.extractall(output_dir)

def execute_in_environment(tmpdir, file, *args):
    new_environment = os.environ.copy()
    new_environment['PATH'] = os.path.join(tmpdir, 'venv/bin') + ':' + os.environ['PATH']
    new_environment['CUDA_VISIBLE_DEVICES'] = '-1'
    new_environment['TF_CPP_MIN_LOG_LEVEL'] = '3'

    cwd = os.path.join(tmpdir, 'DS-1000')

    shutil.copyfile(os.path.join('evaluation/benchmarks', file), os.path.join(cwd, file))

    process_output = subprocess.run(
        ['python3.8', os.path.join(cwd, file), *args],
        env=new_environment,
        cwd=cwd,
        stdout=subprocess.PIPE,
        text=True
    ).stdout

    return json.loads(process_output)

def compute_prompt(problem):
    problem_description = []
    answer_description = []
    answer_code_start = []
    answer_code_end = []

    current_part = None
    for line in problem.split('\n'):
        if current_part is None:
            if line.startswith('Origin'):
                continue
            assert line.startswith('Problem:')
            current_part = 'problem_description'
        elif current_part == 'problem_description':
            if line == 'A:':
                current_part = 'answer_description'
                continue
            problem_description.append(line)
        elif current_part == 'answer_description':
            if line == '<code>':
                current_part = 'answer_code_start'
                continue
            answer_description.append(line)
        elif current_part == 'answer_code_start':
            if line == '</code>':
                current_part = 'solution'
                continue
            answer_code_start.append(line)
        elif current_part == 'solution':
            assert line in [
                'BEGIN SOLUTION',
                '<code>',
                '[insert]',
                '</code>',
                'END SOLUTION'
            ]

            if line == 'END SOLUTION':
                current_part = 'answer_code_end'
        elif current_part == 'answer_code_end':
            if line == '<code>':
                continue
            if line == '</code>':
                current_part = 'end'
                continue
            answer_code_end.append(line)
        elif current_part == 'end':
            if line.strip() == '':
                continue
            raise

    parts = {
        'problem_description': problem_description,
        'answer_description': answer_description,
        'answer_code_start': answer_code_start,
        'answer_code_end': answer_code_end,
    }

    for k, part in parts.items():
        parts[k] = '\n'.join(part).strip().split('\n')

    prompt = '\n'.join([
        ('You will be given a problem description for a python programming problem as well as the solution code with a part missing. '
            'Please solve the problem by filling out the [[Missing]] part of the solution code.'),
        '',
        '[Problem Description]',
        *parts['problem_description'],
        '[End of Problem Description]'
        '',
        '[Solution Code]',
        '```python',
        *parts['answer_code_start'],
        '# [Begin Missing Code]',
        '[[Missing]]',
        '# [End Missing Code]',
        *parts['answer_code_end'],
        '```',
        '[End of Solution Code]'
        '',
        ('Please now fill out the [[Missing]] part of the [Solution Code]. '
            'Include the [Begin Missing Code] and [End Missing Code] to separate the [Missing] part just like in the provided code.'
            'Do not output anything except the [[Missing]] line(s) of code to complete the [Solution Code]. '
            'Do not output any description, explanation or any other text that is not the [[Missing]] code. '),
    ])

    return { **parts, 'prompt': prompt }

def compute_prompts(data):
    prompts = []
    for k, v in data.items():
        for i, problem in enumerate(v):
            prompts.append({ 'part': k, 'index': i, 'problem_description': problem, **compute_prompt(problem) })

    with open('prompts.json', 'w') as f:
        json.dump(prompts, f, indent=4)
    return prompts

def compute_ds1000_model_replies(*, model_type, model_name, model_args, prompts, data, output_path):
    if os.path.exists(output_path):
        return

    model = create_model(model_type, model_name, model_args)

    conversations = [{
        'conversation': [('user', prompt['prompt'])],
        'temperature': 0,
    } for prompt in prompts]

    model_replies_raw = compute_model_replies(model, conversations, progress_bar_description=model_name + ' :: DS-1000 :: Computing model replies')

    model_replies_by_part = {}
    for i, prompt in enumerate(prompts):
        part = prompt['part']
        index = prompt['index']
        if part not in model_replies_by_part:
            model_replies_by_part[part] = [None] * len(data[part])
        model_replies_by_part[part][index] = model_replies_raw[i]

    with open(output_path, 'w') as f:
        json.dump(model_replies_by_part, f, indent=4)

def postprocess_model_reply(model_reply):
    model_reply = model_reply.replace('\r\n', '\n')

    if '```python\n' in model_reply and '```\n' in model_reply:
        model_reply = model_reply.split('```python')[1].split('```')[0]

    if '```python\n' in model_reply and model_reply.endswith('```'):
        model_reply = model_reply.split('```python')[1].split('```')[0]

    if '[Begin Missing Code]' in model_reply:
        model_reply = model_reply.split('[Begin Missing Code]')[1]
    if '# [End Missing Code]' in model_reply:
        model_reply = model_reply.split('# [End Missing Code]')[0]
    if '[End Missing Code]' in model_reply:
        model_reply = model_reply.split('[End Missing Code]')[0]

    lines = []
    for line in model_reply.split('\n'):
        if line.startswith('import'):
            continue
        if line.startswith('print'):
            continue
        lines.append(line)

    return '\n'.join(lines)

def postprocess_model_replies(*, model_replies_output_path, postprocessed_model_replies_output_path):
    with open(model_replies_output_path) as f:
        model_replies = json.load(f)

    postprocessed_model_replies = {}
    for k, v in model_replies.items():
        postprocessed_model_replies[k] = [postprocess_model_reply(e) for e in v]

    with open(postprocessed_model_replies_output_path, 'w') as f:
        json.dump(postprocessed_model_replies, f, indent=4)

def evaluate_model(model_type, model_name, model_args, evaluation_id):
    tmpdir = os.path.join(os.getcwd(), '.tmp/ds1000')
    os.makedirs(tmpdir, exist_ok=True)
    install_ds1000(tmpdir)

    download_ds1000_data(tmpdir)

    data = execute_in_environment(tmpdir, 'ds_1000_load_data.py')
    prompts = compute_prompts(data)

    output_folder = os.path.join('reports/ds1000', model_name_to_filename(model_name), evaluation_id)
    os.makedirs(output_folder, exist_ok=True)
    model_replies_output_path = os.path.join(output_folder, 'answers.json')

    compute_ds1000_model_replies(
        model_type=model_type,
        model_name=model_name,
        model_args=model_args,
        prompts=prompts,
        data=data,
        output_path=model_replies_output_path,
    )

    postprocessed_model_replies_output_path = os.path.join(output_folder, 'answers-postprocessed.json')

    postprocess_model_replies(
        model_replies_output_path=model_replies_output_path,
        postprocessed_model_replies_output_path=postprocessed_model_replies_output_path,
    )

    execution_results = execute_in_environment(
        tmpdir,
        'ds_1000_test_correctness.py',
        os.path.abspath(postprocessed_model_replies_output_path),
        model_name + ' :: DS-1000 :: Checking correctness'
    )

    average_scores = {}
    for k, v in execution_results.items():
        average_scores[k] = sum(v) / len(v)

    average_scores_values = list(average_scores.values())
    total_average_score = sum(average_scores_values) / len(average_scores_values)

    scores = {
        'tasks': average_scores,
        'average': total_average_score,
    }

    print(json.dumps(scores, indent=4))