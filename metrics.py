import numpy as np
import scipy as sp


def softmax(logits):
    exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
    return exp / np.sum(exp, axis=1, keepdims=True)


def inception_score(logits):
    p = softmax(logits)
    q = np.mean(p, axis=0, keepdims=True)
    kl_divergence = np.sum(np.where(p == 0.0, 0.0, p * np.log(p / q)), axis=1)
    return np.exp(np.mean(kl_divergence))


def frechet_inception_distance(real_features, fake_features):
    real_mean = np.mean(real_features, axis=0)
    fake_mean = np.mean(fake_features, axis=0)
    real_cov = np.cov(real_features, rowvar=False)
    fake_cov = np.cov(fake_features, rowvar=False)
    mean_cov = sp.linalg.sqrtm(np.dot(real_cov, fake_cov))
    return np.sum((real_mean - fake_mean) ** 2) + np.trace(real_cov + fake_cov - 2 * mean_cov)
