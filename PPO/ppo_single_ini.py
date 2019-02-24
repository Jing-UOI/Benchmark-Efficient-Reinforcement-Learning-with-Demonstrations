"""
A simple version of Proximal Policy Optimization (PPO) using single thread.
Based on:
1. Emergence of Locomotion Behaviours in Rich Environments (Google Deepmind): [https://arxiv.org/abs/1707.02286]
2. Proximal Policy Optimization Algorithms (OpenAI): [https://arxiv.org/abs/1707.06347]
View more on my tutorial website: https://morvanzhou.github.io/tutorials
Dependencies:
tensorflow r1.2
gym 0.9.2
"""

import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
import gym
from env import Reacher
import time
# np.set_printoptions(precision=4, threshold=np.nan)


EP_MAX = 2000
EP_LEN = 10
GAMMA = 0.9
A_LR = 1e-4
C_LR = 2e-4
# A_LR = 1e-10
# C_LR = 2e-10
BATCH = 64
A_UPDATE_STEPS = 3
C_UPDATE_STEPS = 2
S_DIM, A_DIM = 10,3
METHOD = [
    dict(name='kl_pen', kl_target=0.01, lam=0.5),   # KL penalty
    dict(name='clip', epsilon=0.2),                 # Clipped surrogate objective, find this is better
][1]        # choose the method for optimization


class PPO(object):

    def __init__(self):
        self.sess = tf.Session()
        self.tfs = tf.placeholder(tf.float32, [None, S_DIM], 'state')
        self.sigma_lower_bound=1e-3

        # critic
        with tf.variable_scope('critic'):
            l1 = tf.layers.dense(self.tfs, 100, tf.nn.relu)
            self.v = tf.layers.dense(l1, 1)
            self.tfdc_r = tf.placeholder(tf.float32, [None, 1], 'discounted_r')
            self.advantage = self.tfdc_r - self.v
            self.closs = tf.reduce_mean(tf.square(self.advantage))
            self.ctrain_op = tf.train.AdamOptimizer(C_LR).minimize(self.closs)

        # actor
        pi, pi_params = self._build_anet('pi', trainable=True, sigma_lower_bound=self.sigma_lower_bound)
        oldpi, oldpi_params = self._build_anet('oldpi', trainable=False, sigma_lower_bound=self.sigma_lower_bound)
        with tf.variable_scope('sample_action'):
            self.sample_op = tf.squeeze(pi.sample(1), axis=0)       # choosing action

        with tf.variable_scope('update_oldpi'):
            self.update_oldpi_op = [oldp.assign(p) for p, oldp in zip(pi_params, oldpi_params)]

        self.tfa = tf.placeholder(tf.float32, [None, A_DIM], 'action')
        self.tfadv = tf.placeholder(tf.float32, [None, 1], 'advantage')
        with tf.variable_scope('loss'):
            with tf.variable_scope('surrogate'):
                # ratio = tf.exp(pi.log_prob(self.tfa) - oldpi.log_prob(self.tfa))
                ratio = pi.prob(self.tfa) / oldpi.prob(self.tfa)
                surr = ratio * self.tfadv
            if METHOD['name'] == 'kl_pen':
                self.tflam = tf.placeholder(tf.float32, None, 'lambda')
                kl = tf.distributions.kl_divergence(oldpi, pi)
                self.kl_mean = tf.reduce_mean(kl)
                self.aloss = -(tf.reduce_mean(surr - self.tflam * kl))
            else:   # clipping method, find this is better
                self.aloss = -tf.reduce_mean(tf.minimum(
                    surr,
                    tf.clip_by_value(ratio, 1.-METHOD['epsilon'], 1.+METHOD['epsilon'])*self.tfadv))
            # add entropy to boost exploration
            # entropy=pi.entropy()
            # self.aloss-=0.1*entropy
        with tf.variable_scope('atrain'):
            self.atrain_op = tf.train.AdamOptimizer(A_LR).minimize(self.aloss)

        tf.summary.FileWriter("log/", self.sess.graph)


        self.sess.run(tf.global_variables_initializer())
        ''' initialize the actor policy, both pi and oldpi'''
        self.load_ini()
        self.sess.run(self.update_oldpi_op)

        # check if loaded correctly
        # actor_var_list=tf.contrib.framework.get_variables('pi')
        # print(self.sess.run(actor_var_list))
        


    # def load_ini(self, ):
    #     actor_var_list=tf.contrib.framework.get_variables('pi')
    #     print(actor_var_list)
    #     actor_saver=tf.train.Saver(actor_var_list)
    #     actor_saver.restore(self.sess, './ini/ppo_fixed')
    #     print('Actor Load Succeed!')


    def load_ini(self, ):
        variables = tf.contrib.framework.get_variables_to_restore()
        # dense_5 is layer of sigma, sigma is fixed for pretrained
        actor_var_list = [v for v in variables if (v.name.split('/')[0]=='pi' and v.name.split('/')[1]!='dense_5')]
        # print(actor_var_list)
        actor_saver=tf.train.Saver(actor_var_list)
        actor_saver.restore(self.sess, './ini/ppo_fixed')
        print('Actor Load Succeed!')

    def pretrain_critic(self,s,a, r):
        [self.sess.run(self.ctrain_op, {self.tfs: s, self.tfdc_r: r}) for _ in range(C_UPDATE_STEPS)]



    def update(self, s, a, r):
        print(s,a,r)
        self.sess.run(self.update_oldpi_op)
        adv = self.sess.run(self.advantage, {self.tfs: s, self.tfdc_r: r})
        adv = (adv - adv.mean())/(adv.std()+1e-6)     # sometimes helpful

        # update actor
        if METHOD['name'] == 'kl_pen':
            for i in range(A_UPDATE_STEPS):
                print('updata: ',i)
                _, kl = self.sess.run(
                    [self.atrain_op, self.kl_mean],
                    {self.tfs: s, self.tfa: a, self.tfadv: adv, self.tflam: METHOD['lam']})
                if kl > 4*METHOD['kl_target']:  # this in in google's paper
                    break
            if kl < METHOD['kl_target'] / 1.5:  # adaptive lambda, this is in OpenAI's paper
                METHOD['lam'] /= 2
            elif kl > METHOD['kl_target'] * 1.5:
                METHOD['lam'] *= 2
            METHOD['lam'] = np.clip(METHOD['lam'], 1e-4, 10)    # sometimes explode, this clipping is my solution
        else:   # clipping method, find this is better (OpenAI's paper)
            # [self.sess.run(self.atrain_op, {self.tfs: s, self.tfa: a, self.tfadv: adv}) for _ in range(A_UPDATE_STEPS)]
            for _ in range(A_UPDATE_STEPS):
                loss,_= self.sess.run([self.aloss, self.atrain_op], {self.tfs: s, self.tfa: a, self.tfadv: adv})
                print('loss: ', loss)

        # update critic
        [self.sess.run(self.ctrain_op, {self.tfs: s, self.tfdc_r: r}) for _ in range(C_UPDATE_STEPS)]

    def _build_anet(self, name, trainable, sigma_lower_bound):
        with tf.variable_scope(name):
            l1 = tf.layers.dense(self.tfs, 100, tf.nn.relu, trainable=trainable, kernel_initializer=tf.contrib.layers.xavier_initializer())
            l1 = tf.layers.dense(l1, 100, tf.nn.relu, trainable=trainable, kernel_initializer=tf.contrib.layers.xavier_initializer())
            l1 = tf.layers.dense(l1, 100, tf.nn.relu, trainable=trainable, kernel_initializer=tf.contrib.layers.xavier_initializer())
            l1 = tf.layers.dense(l1, 100, tf.nn.tanh, trainable=trainable, kernel_initializer=tf.contrib.layers.xavier_initializer())

            # l1 = tf.layers.batch_normalization(tf.layers.dense(self.tfs, 100, tf.nn.relu, trainable=trainable), training=True)
            '''the action mean mu is set to be scale 10 instead of 360, avoiding useless shaking and one-step to goal!'''
            mu =  30.*tf.layers.dense(l1, A_DIM, tf.nn.tanh, trainable=trainable, kernel_initializer=tf.contrib.layers.xavier_initializer())
            # softplus as activation
            sigma = 0.01* tf.layers.dense(l1, A_DIM, tf.nn.sigmoid, trainable=trainable, kernel_initializer=tf.contrib.layers.xavier_initializer()) # softplus to make it positive
            # in case that sigma is 0
            sigma +=sigma_lower_bound  #1e-4
            self.mu=mu
            self.sigma=sigma
            norm_dist = tf.distributions.Normal(loc=mu, scale=sigma)

        params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=name)
        return norm_dist, params

    def choose_action(self, s):
        s = s[np.newaxis, :]
        a ,mu, sigma= self.sess.run([self.sample_op, self.mu, self.sigma], {self.tfs: s})
        # print('s: ',s)
        print('a: ', a)
        print('mu, sigma: ', mu,sigma)
        return np.clip(a[0], -360, 360)


    def pretrain_choose_action(self, s):
        s = s[np.newaxis, :]
        a = self.sess.run(self.sample_op, {self.tfs: s})
        # add noise
        a[0] += 10*np.random.rand(len(a[0]))
        return np.clip(a[0], -360, 360)

    def get_v(self, s):
        if s.ndim < 2: s = s[np.newaxis, :]
        return self.sess.run(self.v, {self.tfs: s})[0, 0]

env=Reacher(render=True)
ppo = PPO()
all_ep_r = []

'''pretrain the critic'''
PRE_TRAIN = 8

for ep in range(PRE_TRAIN):
    print('pretrain step: {}'.format(ep))
    s = env.reset()
    # s=s/100.
    buffer_s, buffer_a, buffer_r = [], [], []
    ep_r = 0
    for t in range(EP_LEN):    # in one episode
        # env.render()
        a = ppo.pretrain_choose_action(s)
        # time.sleep(3)
        s_, r, done = env.step(a)
        # s_=s_/100.
        buffer_s.append(s)
        buffer_a.append(a)
        # print('r, norm_r: ', r, (r+8)/8)
        '''the normalization makes reacher's reward almost same and not work'''
        # buffer_r.append((r+8)/8)    # normalize reward, find to be useful
        buffer_r.append(r)
        s = s_
        ep_r += r

        # update ppo
        if (t+1) % BATCH == 0 or t == EP_LEN-1:
            v_s_ = ppo.get_v(s_)
            discounted_r = []
            for r in buffer_r[::-1]:
                v_s_ = r + GAMMA * v_s_
                discounted_r.append(v_s_)
            discounted_r.reverse()

            bs, ba, br = np.vstack(buffer_s), np.vstack(buffer_a), np.array(discounted_r)[:, np.newaxis]
            buffer_s, buffer_a, buffer_r = [], [], []
            ppo.pretrain_critic(bs, ba, br)

print('pretrain finished!')


for ep in range(EP_MAX):
    s = env.reset()
    # s=s/100.
    buffer_s, buffer_a, buffer_r = [], [], []
    ep_r = 0
    for t in range(EP_LEN):    # in one episode
        # env.render()
        a = ppo.choose_action(s)
        s_, r, done = env.step(a)
        # s_=s_/100.
        buffer_s.append(s)
        buffer_a.append(a)
        # print('r, norm_r: ', r, (r+8)/8)
        '''the normalization makes reacher's reward almost same and not work'''
        # buffer_r.append((r+8)/8)    # normalize reward, find to be useful
        buffer_r.append(r)
        s = s_
        ep_r += r

        # update ppo
        if (t+1) % BATCH == 0 or t == EP_LEN-1:
            v_s_ = ppo.get_v(s_)
            discounted_r = []
            for r in buffer_r[::-1]:
                v_s_ = r + GAMMA * v_s_
                discounted_r.append(v_s_)
            discounted_r.reverse()

            bs, ba, br = np.vstack(buffer_s), np.vstack(buffer_a), np.array(discounted_r)[:, np.newaxis]
            buffer_s, buffer_a, buffer_r = [], [], []
            ppo.update(bs, ba, br)
    if ep == 0: all_ep_r.append(ep_r)
    else: all_ep_r.append(all_ep_r[-1]*0.9 + ep_r*0.1)
    print(
        'Ep: %i' % ep,
        "|Ep_r: %i" % ep_r,
        ("|Lam: %.4f" % METHOD['lam']) if METHOD['name'] == 'kl_pen' else '',
    )
    if ep % 50==0:
        plt.plot(np.arange(len(all_ep_r)), all_ep_r)
        plt.xlabel('Episode');plt.ylabel('Moving averaged episode reward');plt.savefig('./ppo_single_ini.png')


print('all_ep_r: ',all_ep_r)