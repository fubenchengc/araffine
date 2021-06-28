import argparse
import os

import gym
import numpy as np
from stable_baselines.common import set_global_seeds

try:
    import pybullet_envs
except importError:
    pybullet_envs = None

from utils import ALGOS, create_test_env

parser = argparse.ArgumentParser()
parser.add_argument('--env', help='environment ID', type=str, default='CartPole-v1')
parser.add_argument('-f', '--folder', help='Log folder', type=str, default='trained_agents')
parser.add_argument('--algo', help='RL Algorithm', default='ppo2',
                    type=str, required=False, choices=list(ALGOS.keys()))
parser.add_argument('-n', '--n-timesteps', help='number of timesteps', default=1000,
                    type=int)
parser.add_argument('--n-envs', help='number of environments', default=1,
                    type=int)
parser.add_argument('--no-render', action='store_true', default=False,
                    help='Do not render the environment (useful for tests)')
parser.add_argument('--deterministic', action='store_true', default=False,
                    help='Use deterministic actions')
parser.add_argument('--norm-reward', action='store_true', default=False,
                    help='Normalize reward if applicable (trained with VecNormalize)')
parser.add_argument('--seed', help='Random generator seed', type=int, default=0)

args = parser.parse_args()

env_id = args.env
algo = args.algo
folder = args.folder
model_path = "{}/{}/{}.pkl".format(folder, algo, env_id)

# Sanity checks
assert os.path.isdir(folder + '/' + algo), "The {}/{}/ folder was not found".format(folder, algo)
assert os.path.isfile(model_path), "No model found for {} on {}, path: {}".format(algo, env_id, model_path)

if algo in ['dqn', 'ddpg']:
    args.n_envs = 1

set_global_seeds(args.seed)

is_atari = 'NoFrameskip' in env_id

stats_path = "{}/{}/{}/".format(folder, algo, env_id)
if not os.path.isdir(stats_path):
    stats_path = None
using_vec_normalize = stats_path is not None

env = create_test_env(env_id, n_envs=args.n_envs, is_atari=is_atari,
                      stats_path=stats_path, norm_reward=args.norm_reward,
                      seed=args.seed)

model = ALGOS[algo].load(model_path)

obs = env.reset()

# Force deterministic for DQN and DDPG
deterministic = args.deterministic or algo in ['dqn', 'ddpg']

running_reward = 0.0
ep_len = 0
for _ in range(args.n_timesteps):
    action, _ = model.predict(obs, deterministic=deterministic)
    # Random Agent
    # action = [env.action_space.sample()]
    # Clip Action to avoid out of bound errors
    if isinstance(env.action_space, gym.spaces.Box):
        action = np.clip(action, env.action_space.low, env.action_space.high)
    obs, reward, done, infos = env.step(action)
    if not args.no_render:
        env.render()
    running_reward += reward[0]
    ep_len += 1

    if args.n_envs == 1:
        # For atari the return reward is not the atari score
        # so we have to get it from the infos dict
        if infos is not None:
            episode_infos = infos[0].get('episode')
            if episode_infos is not None:
                print("Atari Episode Score: {:.2f}".format(episode_infos['r']))
                print("Atari Episode Length", episode_infos['l'])

        if done and not is_atari:
            # NOTE: for env using VecNormalize, the mean reward
            # is a normalized reward when `--norm_reward` flag is passed
            print("Episode Reward: {:.2f}".format(running_reward))
            print("Episode Length", ep_len)
            running_reward = 0.0
            ep_len = 0

# Workaround for https://github.com/openai/gym/issues/893
if not args.no_render:
    if is_atari:
        env.close()
    elif using_vec_normalize:
        env.venv.envs[0].env.close()
    else:
        env.envs[0].env.close()
