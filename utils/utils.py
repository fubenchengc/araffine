import time
import os
import glob
import inspect
import glob
import yaml

import gym
import pybullet_envs
from gym.envs.registration import load
from stable_baselines.deepq.policies import FeedForwardPolicy
from stable_baselines.common.policies import FeedForwardPolicy as BasePolicy
from stable_baselines.common.policies import register_policy
from stable_baselines.bench import Monitor
from stable_baselines import logger
from stable_baselines import PPO2, A2C, ACER, ACKTR, DQN, DDPG
# Temp fix until SAC is integrated into stable_baselines
try:
    from stable_baselines import SAC
except ImportError:
    SAC = None
from stable_baselines.common.vec_env import DummyVecEnv, VecNormalize, \
    VecFrameStack, SubprocVecEnv
from stable_baselines.common.cmd_util import make_atari_env
from stable_baselines.common import set_global_seeds

ALGOS = {
    'a2c': A2C,
    'acer': ACER,
    'acktr': ACKTR,
    'dqn': DQN,
    'ddpg': DDPG,
    'sac': SAC,
    'ppo2': PPO2
}


# ================== Custom Policies =================

class CustomDQNPolicy(FeedForwardPolicy):
    def __init__(self, *args, **kwargs):
        super(CustomDQNPolicy, self).__init__(*args, **kwargs,
                                              layers=[64],
                                              layer_norm=True,
                                              feature_extraction="mlp")

class CustomMlpPolicy(BasePolicy):
    def __init__(self, *args, **kwargs):
        super(CustomMlpPolicy, self).__init__(*args, **kwargs,
                                              layers=[16],
                                              feature_extraction="mlp")

if SAC is not None:
    from stable_baselines.sac.policies import FeedForwardPolicy as SACPolicy

    class CustomSACPolicy(SACPolicy):
        def __init__(self, *args, **kwargs):
            super(CustomSACPolicy, self).__init__(*args, **kwargs,
                                                  layers=[256, 256],
                                                  feature_extraction="mlp")
    register_policy('CustomSACPolicy', CustomSACPolicy)

register_policy('CustomDQNPolicy', CustomDQNPolicy)
register_policy('CustomMlpPolicy', CustomMlpPolicy)


def make_env(env_id, rank=0, seed=0, log_dir=None):
    """
    Helper function to multiprocess training
    and log the progress.

    :param env_id: (str)
    :param rank: (int)
    :param seed: (int)
    :param log_dir: (str)
    """
    if log_dir is None and log_dir != '':
        log_dir = "/tmp/gym/{}/".format(int(time.time()))
    os.makedirs(log_dir, exist_ok=True)

    def _init():
        set_global_seeds(seed + rank)
        env = gym.make(env_id)
        env.seed(seed + rank)
        env = Monitor(env, os.path.join(log_dir, str(rank)), allow_early_resets=True)
        return env

    return _init


def create_test_env(env_id, n_envs=1, is_atari=False,
                    stats_path=None, seed=0,
                    log_dir='', should_render=True, hyperparams=None):
    """
    Create environment for testing a trained agent

    :param env_id: (str)
    :param n_envs: (int) number of processes
    :param is_atari: (bool)
    :param stats_path: (str) path to folder containing saved running averaged
    :param seed: (int) Seed for random number generator
    :param log_dir: (str) Where to log rewards
    :param should_render: (bool) For Pybullet env, display the GUI
    :parma hyperparams: (dict) Additional hyperparams (ex: n_stack)
    :return: (gym.Env)
    """
    # HACK to save logs
    if log_dir is not None:
        os.environ["OPENAI_LOG_FORMAT"] = 'csv'
        os.environ["OPENAI_LOGDIR"] = os.path.abspath(log_dir)
        os.makedirs(log_dir, exist_ok=True)
        logger.configure()

    # Create the environment and wrap it if necessary
    if is_atari:
        print("Using Atari wrapper")
        env = make_atari_env(env_id, num_env=n_envs, seed=seed)
        # Frame-stacking with 4 frames
        env = VecFrameStack(env, n_stack=4)
    elif n_envs > 1:
        env = SubprocVecEnv([make_env(env_id, i, seed, log_dir) for i in range(n_envs)])
    # Pybullet envs does not follow gym.render() interface
    elif "Bullet" in env_id:
        spec = gym.envs.registry.env_specs[env_id]
        class_ = load(spec._entry_point)
        # HACK: force SubprocVecEnv for Bullet env that does not
        # have a render argument
        use_subproc = 'renders' not in inspect.getfullargspec(class_.__init__).args

        # Create the env, with the original kwargs, and the new ones overriding them if needed
        def _init():
            # TODO: fix for pybullet locomotion envs
            env = class_(**{**spec._kwargs}, renders=should_render)
            env.seed(0)
            if log_dir is not None:
                env = Monitor(env, os.path.join(log_dir, "0"), allow_early_resets=True)
            return env

        if use_subproc:
            env = SubprocVecEnv([make_env(env_id, 0, seed, log_dir)])
        else:
            env = DummyVecEnv([_init])
    else:
        env = DummyVecEnv([make_env(env_id, 0, seed, log_dir)])

    # Load saved stats for normalizing input and rewards
    # And optionally stack frames
    if stats_path is not None:
        if hyperparams['normalize']:
            print("Loading running average")
            print("with params: {}".format(hyperparams['normalize_kwargs']))
            env = VecNormalize(env, training=False, **hyperparams['normalize_kwargs'])
            env.load_running_average(stats_path)

        n_stack = hyperparams.get('n_stack', 0)
        if n_stack > 0:
            print("Stacking {} frames".format(n_stack))
            env = VecFrameStack(env, n_stack)
    return env


def linear_schedule(initial_value):
    """
    Linear learning rate schedule.

    :param initial_value: (float or str)
    :return: (function)
    """
    if isinstance(initial_value, str):
        initial_value = float(initial_value)

    def func(progress):
        """
        Progress will decrease from 1 (beginning) to 0
        :param progress: (float)
        :return: (float)
        """
        return progress * initial_value

    return func


def get_trained_models(log_folder):
    """
    :param log_folder: (str) Root log folder
    :return: (dict) Dict representing the trained agent
    """
    algos = os.listdir(log_folder)
    trained_models = {}
    for algo in algos:
        for env_id in glob.glob('{}/{}/*.pkl'.format(log_folder, algo)):
            # Retrieve env name
            env_id = env_id.split('/')[-1].split('.pkl')[0]
            trained_models['{}-{}'.format(algo, env_id)] = (algo, env_id)
    return trained_models


def get_latest_run_id(log_path, env_id):
    """
    Returns the latest run number for the given log name and log path,
    by finding the greatest number in the directories.

    :param log_path: (str) path to log folder
    :param env_id: (str)
    :return: (int) latest run number
    """
    max_run_id = 0
    for path in glob.glob(log_path + "/{}_[0-9]*".format(env_id)):
        file_name = path.split("/")[-1]
        ext = file_name.split("_")[-1]
        if env_id == "_".join(file_name.split("_")[:-1]) and ext.isdigit() and int(ext) > max_run_id:
            max_run_id = int(ext)
    return max_run_id

def get_saved_hyperparams(stats_path, norm_reward=False):
    """
    :param stats_path: (str)
    :param norm_reward: (bool)
    :return: (dict, str)
    """
    hyperparams = {}
    if not os.path.isdir(stats_path):
        stats_path = None
    else:
        config_file = os.path.join(stats_path, 'config.yml')
        if os.path.isfile(config_file):
            # Load saved hyperparameters
            with open(os.path.join(stats_path, 'config.yml'), 'r') as f:
                hyperparams = yaml.load(f)
            hyperparams['normalize'] = hyperparams.get('normalize', False)
        else:
            obs_rms_path = os.path.join(stats_path, 'obs_rms.pkl')
            hyperparams['normalize'] = os.path.isfile(obs_rms_path)

        # Load normalization params
        normalize_kwargs = {}
        if hyperparams['normalize']:
            if isinstance(hyperparams['normalize'], str):
                normalize_kwargs = eval(hyperparams['normalize'])
            else:
                normalize_kwargs = {'norm_obs': hyperparams['normalize'], 'norm_reward': norm_reward}
            hyperparams['normalize_kwargs'] = normalize_kwargs
    return hyperparams, stats_path
