"""
Reversible Instance Normalization (RevIN) for Financial and Crypto Time-Series.
Addresses distribution shift, local non-stationarity, and regime-dependent price scales
without suffering from lookahead bias or data leakage.

Reference:
Kim et al., 'Reversible Instance Normalization for Accurate Time-Series Forecasting
against Distribution Shift', ICLR 2022.
"""

from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


class RevINNormalizer:
    """
    Reversible Instance Normalization (RevIN) implementation for NumPy and Pandas.
    Preserves instance-level statistics (mean, stdev) per window to normalize input series,
    and supports exact inverse mapping to restore predictions to genuine price scales.
    """

    def __init__(
        self,
        num_features: int = 1,
        eps: float = 1e-5,
        affine: bool = True,
        gamma_init: float = 1.0,
        beta_init: float = 0.0,
    ):
        self.num_features = num_features
        self.eps = eps
        self.affine = affine

        # Affine parameters: scale (gamma) and shift (beta)
        self.gamma: Optional[np.ndarray]
        self.beta: Optional[np.ndarray]
        if affine:
            self.gamma = np.full(num_features, gamma_init, dtype=np.float64)
            self.beta = np.full(num_features, beta_init, dtype=np.float64)
        else:
            self.gamma = None
            self.beta = None

        # Instance state cache
        self.last_mean: Optional[np.ndarray] = None
        self.last_stdev: Optional[np.ndarray] = None

    def fit_transform(
        self,
        x: Union[np.ndarray, pd.DataFrame],
        columns: Optional[List[str]] = None,
    ) -> Union[np.ndarray, pd.DataFrame]:
        """
        Calculates instance mean and standard deviation along the time axis (axis=0)
        and normalizes the series.
        """
        is_df = isinstance(x, pd.DataFrame)
        if is_df:
            cols = columns or list(x.columns)
            arr = x[cols].to_numpy(dtype=np.float64)
        else:
            arr = np.asarray(x, dtype=np.float64)

        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)

        # Instance mean and stdev across time window
        mean = np.nanmean(arr, axis=0, keepdims=True)
        var = np.nanvar(arr, axis=0, keepdims=True)
        stdev = np.sqrt(var + self.eps)

        self.last_mean = mean
        self.last_stdev = stdev

        # Normalize
        norm_arr = (arr - mean) / stdev

        # Apply affine transformation if enabled
        if self.affine and self.gamma is not None and self.beta is not None:
            gamma = self.gamma.reshape(1, -1)
            beta = self.beta.reshape(1, -1)
            norm_arr = norm_arr * gamma + beta

        if is_df:
            res_df = x.copy()
            res_df[cols] = norm_arr
            return res_df
        return norm_arr

    def inverse_transform(
        self,
        x_norm: Union[np.ndarray, pd.DataFrame],
        target_idx: Optional[Union[int, List[int]]] = None,
    ) -> Union[np.ndarray, pd.DataFrame]:
        """
        Reverses normalization using the stored instance statistics.
        If target_idx is provided, de-normalizes only the selected feature dimension(s) (e.g. Close price).
        """
        if self.last_mean is None or self.last_stdev is None:
            raise ValueError("RevIN must be fit before inverse_transform can be called.")

        is_df = isinstance(x_norm, pd.DataFrame)
        if is_df:
            arr = x_norm.to_numpy(dtype=np.float64)
        else:
            arr = np.asarray(x_norm, dtype=np.float64)

        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)

        mean = self.last_mean
        stdev = self.last_stdev

        if target_idx is not None:
            if isinstance(target_idx, int):
                target_idx = [target_idx]
            mean = mean[:, target_idx]
            stdev = stdev[:, target_idx]
            if self.affine and self.gamma is not None and self.beta is not None:
                gamma = self.gamma[target_idx].reshape(1, -1)
                beta = self.beta[target_idx].reshape(1, -1)
            else:
                gamma, beta = None, None
        else:
            gamma = self.gamma.reshape(1, -1) if self.affine and self.gamma is not None else None
            beta = self.beta.reshape(1, -1) if self.affine and self.beta is not None else None

        # Invert affine
        if self.affine and gamma is not None and beta is not None:
            arr = (arr - beta) / (gamma + 1e-8)

        # Invert normalization
        orig = arr * stdev + mean

        if is_df:
            return pd.DataFrame(orig, columns=x_norm.columns, index=x_norm.index)
        return orig

    def create_sliding_windows(
        self,
        df: pd.DataFrame,
        feature_cols: List[str],
        window_size: int = 30,
        stride: int = 1,
    ) -> Tuple[np.ndarray, List[Dict[str, float]]]:
        """
        Generates strictly causal rolling windows where each window is normalized
        by its OWN instance statistics (Zero Lookahead Bias).
        """
        data = df[feature_cols].to_numpy(dtype=np.float64)
        n_samples = len(data)

        if n_samples < window_size:
            return np.empty((0, window_size, len(feature_cols))), []

        windows = []
        stats = []

        for i in range(0, n_samples - window_size + 1, stride):
            window = data[i : i + window_size]
            mean = np.nanmean(window, axis=0, keepdims=True)
            stdev = np.sqrt(np.nanvar(window, axis=0, keepdims=True) + self.eps)
            norm_win = (window - mean) / stdev

            windows.append(norm_win)
            stats.append({
                "start_idx": i,
                "end_idx": i + window_size - 1,
                "mean": mean.squeeze().tolist(),
                "stdev": stdev.squeeze().tolist(),
            })

        return np.array(windows), stats
