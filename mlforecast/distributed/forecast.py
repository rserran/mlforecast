# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/distributed.forecast.ipynb.

# %% auto 0
__all__ = ['DistributedMLForecast', 'DistributedForecast']

# %% ../../nbs/distributed.forecast.ipynb 5
import warnings
from typing import Callable, Iterable, List, Optional

import dask.dataframe as dd
import numpy as np
import pandas as pd
from dask.distributed import Client, default_client
from sklearn.base import clone

from mlforecast.core import (
    DateFeature,
    Differences,
    Freq,
    LagTransforms,
    Lags,
    Models,
    TimeSeries,
    _name_models,
)
from .core import DistributedTimeSeries
from ..utils import backtest_splits

# %% ../../nbs/distributed.forecast.ipynb 7
class DistributedMLForecast:
    """Distributed pipeline encapsulation."""

    def __init__(
        self,
        models: Models,
        freq: Optional[Freq] = None,
        lags: Optional[Lags] = None,
        lag_transforms: Optional[LagTransforms] = None,
        date_features: Optional[Iterable[DateFeature]] = None,
        differences: Optional[Differences] = None,
        num_threads: int = 1,
        client: Optional[Client] = None,
    ):
        """Create distributed forecast object

        Parameters
        ----------
        models : regressor or list of regressors
            Models that will be trained and used to compute the forecasts.
        freq : str or int, optional (default=None)
            Pandas offset alias, e.g. 'D', 'W-THU' or integer denoting the frequency of the series.
        lags : list of int, optional (default=None)
            Lags of the target to use as features.
        lag_transforms : dict of int to list of functions, optional (default=None)
            Mapping of target lags to their transformations.
        date_features : list of str or callable, optional (default=None)
            Features computed from the dates. Can be pandas date attributes or functions that will take the dates as input.
        differences : list of int, optional (default=None)
            Differences to take of the target before computing the features. These are restored at the forecasting step.
        num_threads : int (default=1)
            Number of threads to use when computing the features.
        client : dask distributed client
            Client to use for computing data and training the models.
        """
        if not isinstance(models, dict) and not isinstance(models, list):
            models = [models]
        if isinstance(models, list):
            model_names = _name_models([m.__class__.__name__ for m in models])
            models_with_names = dict(zip(model_names, models))
        else:
            models_with_names = models
        self.models = models_with_names
        self.client = client or default_client()
        self.dts = DistributedTimeSeries(
            TimeSeries(
                freq, lags, lag_transforms, date_features, differences, num_threads
            ),
            self.client,
        )

    def __repr__(self) -> str:
        return (
            f'{self.__class__.__name__}(models=[{", ".join(self.models.keys())}], '
            f"freq={self.freq}, "
            f"lag_features={list(self.dts._base_ts.transforms.keys())}, "
            f"date_features={self.dts._base_ts.date_features}, "
            f"num_threads={self.dts._base_ts.num_threads}, "
            f"client={self.client})"
        )

    @property
    def freq(self):
        return self.dts._base_ts.freq

    def preprocess(
        self,
        data: dd.DataFrame,
        id_col: str = "index",
        time_col: str = "ds",
        target_col: str = "y",
        static_features: Optional[List[str]] = None,
        dropna: bool = True,
        keep_last_n: Optional[int] = None,
    ) -> dd.DataFrame:
        """Add the features to `data`.

        Parameters
        ----------
        data : dask DataFrame
            Series data in long format.
        id_col : str
            Column that identifies each serie. If 'index' then the index is used.
        time_col : str
            Column that identifies each timestep, its values can be timestamps or integers.
        target_col : str
            Column that contains the target.
        static_features : list of str, optional (default=None)
            Names of the features that are static and will be repeated when forecasting.
        dropna : bool (default=True)
            Drop rows with missing values produced by the transformations.
        keep_last_n : int, optional (default=None)
            Keep only these many records from each serie for the forecasting step. Can save time and memory if your features allow it.

        Returns
        -------
        result : dask DataFrame.
            `data` plus added features.
        """
        if id_col in data:
            warnings.warn(
                "It is recommended to have id_col as the index, since setting the index is a slow operation."
            )
            data = data.set_index(id_col)
            id_col = "index"
        return self.dts.fit_transform(
            data, id_col, time_col, target_col, static_features, dropna, keep_last_n
        )

    def fit_models(
        self,
        X: dd.DataFrame,
        y: dd.Series,
    ) -> "DistributedMLForecast":
        """Manually train models. Use this if you called `Forecast.preprocess` beforehand.

        Parameters
        ----------
        X : dask DataFrame
            Features.
        y : dask Series.
            Target.

        Returns
        -------
        self : DistributedForecast
            Forecast object with trained models.
        """
        self.models_ = {}
        for name, model in self.models.items():
            self.models_[name] = clone(model).fit(X, y).model_
        return self

    def fit(
        self,
        data: dd.DataFrame,
        id_col: str,
        time_col: str,
        target_col: str,
        static_features: Optional[List[str]] = None,
        dropna: bool = True,
        keep_last_n: Optional[int] = None,
    ) -> "DistributedMLForecast":
        """Apply the feature engineering and train the models.

        Parameters
        ----------
        data : dask DataFrame
            Series data in long format.
        id_col : str
            Column that identifies each serie. If 'index' then the index is used.
        time_col : str
            Column that identifies each timestep, its values can be timestamps or integers.
        target_col : str
            Column that contains the target.
        static_features : list of str, optional (default=None)
            Names of the features that are static and will be repeated when forecasting.
        dropna : bool (default=True)
            Drop rows with missing values produced by the transformations.
        keep_last_n : int, optional (default=None)
            Keep only these many records from each serie for the forecasting step. Can save time and memory if your features allow it.

        Returns
        -------
        self : DistributedForecast
            Forecast object with series values and trained models.
        """
        train_ddf = self.preprocess(
            data, id_col, time_col, target_col, static_features, dropna, keep_last_n
        )
        X, y = train_ddf.drop(columns=[time_col, target_col]), train_ddf[target_col]
        self.fit_models(X, y)
        return self

    def predict(
        self,
        horizon: int,
        dynamic_dfs: Optional[List[pd.DataFrame]] = None,
        before_predict_callback: Optional[Callable] = None,
        after_predict_callback: Optional[Callable] = None,
        new_data: Optional[dd.DataFrame] = None,
    ) -> dd.DataFrame:
        """Compute the predictions for the next `horizon` steps.

        Parameters
        ----------
        horizon : int
            Number of periods to predict.
        dynamic_dfs : list of pandas DataFrame, optional (default=None)
            Future values of the dynamic features, e.g. prices.
        before_predict_callback : callable, optional (default=None)
            Function to call on the features before computing the predictions.
                This function will take the input dataframe that will be passed to the model for predicting and should return a dataframe with the same structure.
                The series identifier is on the index.
        after_predict_callback : callable, optional (default=None)
            Function to call on the predictions before updating the targets.
                This function will take a pandas Series with the predictions and should return another one with the same structure.
                The series identifier is on the index.
        new_data : pandas DataFrame, optional (default=None)
            Series data of new observations for which forecasts are to be generated.
                This dataframe should have the same structure as the one used to fit the model, including any features and time series data.
                If `new_data` is not None, the method will generate forecasts for the new observations.


        Returns
        -------
        result : dask DataFrame
            Predictions for each serie and timestep, with one column per model.
        """
        if new_data is not None:
            ts_info = self.dts.ts[0].result()
            if ts_info.id_col in new_data:
                warnings.warn(
                    "It is recommended to have id_col as the index, since setting the index is a slow operation."
                )
                new_data = new_data.set_index(ts_info.id_col)
                ts_info.id_col = "index"
            new_dts = DistributedTimeSeries(
                TimeSeries(
                    self.dts._base_ts.freq,
                    self.dts._base_ts.lags,
                    self.dts._base_ts.lag_transforms,
                    self.dts._base_ts.date_features,
                    self.dts._base_ts.differences,
                    self.dts._base_ts.num_threads,
                ),
                self.client,
            )
            new_dts.fit_transform(
                new_data,
                ts_info.id_col,
                ts_info.time_col,
                ts_info.target_col,
                ts_info.static_features.columns,
                ts_info.dropna,
                ts_info.keep_last_n,
            )
            dts = new_dts
        else:
            dts = self.dts

        return dts.predict(
            self.models_,
            horizon,
            dynamic_dfs,
            before_predict_callback,
            after_predict_callback,
        )

    def cross_validation(
        self,
        data: pd.DataFrame,
        n_windows: int,
        window_size: int,
        id_col: str,
        time_col: str,
        target_col: str,
        step_size: Optional[int] = None,
        static_features: Optional[List[str]] = None,
        dropna: bool = True,
        keep_last_n: Optional[int] = None,
        refit: bool = True,
        dynamic_dfs: Optional[List[pd.DataFrame]] = None,
        before_predict_callback: Optional[Callable] = None,
        after_predict_callback: Optional[Callable] = None,
    ):
        """Perform time series cross validation.
        Creates `n_windows` splits where each window has `window_size` test periods,
        trains the models, computes the predictions and merges the actuals.

        Parameters
        ----------
        data : dask DataFrame
            Series data in long format.
        n_windows : int
            Number of windows to evaluate.
        window_size : int
            Number of test periods in each window.
        id_col : str
            Column that identifies each serie. If 'index' then the index is used.
        time_col : str
            Column that identifies each timestep, its values can be timestamps or integers.
        target_col : str
            Column that contains the target.
        step_size : int, optional (default=None)
            Step size between each cross validation window. If None it will be equal to `window_size`.
        static_features : list of str, optional (default=None)
            Names of the features that are static and will be repeated when forecasting.
        dropna : bool (default=True)
            Drop rows with missing values produced by the transformations.
        keep_last_n : int, optional (default=None)
            Keep only these many records from each serie for the forecasting step. Can save time and memory if your features allow it.
        refit : bool (default=True)
            Retrain model for each cross validation window.
            If False, the models are trained at the beginning and then used to predict each window.
        dynamic_dfs : list of pandas DataFrame, optional (default=None)
            Future values of the dynamic features, e.g. prices.
        before_predict_callback : callable, optional (default=None)
            Function to call on the features before computing the predictions.
                This function will take the input dataframe that will be passed to the model for predicting and should return a dataframe with the same structure.
                The series identifier is on the index.
        after_predict_callback : callable, optional (default=None)
            Function to call on the predictions before updating the targets.
                This function will take a pandas Series with the predictions and should return another one with the same structure.
                The series identifier is on the index.

        Returns
        -------
        result : dask DataFrame
            Predictions for each window with the series id, timestamp, last train date, target value and predictions from each model.
        """
        results = []
        self.cv_models_ = []
        if id_col != "index":
            data = data.set_index(id_col)

        def renames(df):
            mapper = {time_col: "ds", target_col: "y"}
            df = df.rename(columns=mapper, copy=False)
            df.index.name = "unique_id"
            return df

        data = data.map_partitions(renames)

        if np.issubdtype(data["ds"].dtype.type, np.integer):
            freq = 1
        else:
            freq = self.freq

        splits = backtest_splits(data, n_windows, window_size, freq, step_size)

        for i_window, (train_end, train, valid) in enumerate(splits):
            if refit or i_window == 0:
                self.fit(
                    train, "index", "ds", "y", static_features, dropna, keep_last_n
                )
            self.cv_models_.append(self.models_)
            y_pred = self.predict(
                window_size,
                dynamic_dfs,
                before_predict_callback,
                after_predict_callback,
                new_data=train if not refit else None,
            )
            result = valid[["ds", "y"]].copy()
            result["cutoff"] = train_end

            def merge_fn(res, pred):
                return res.merge(pred, on=["unique_id", "ds"], how="left")

            meta = {**result.dtypes.to_dict(), **y_pred.dtypes.to_dict()}
            result = result.map_partitions(
                merge_fn, y_pred, align_dataframes=False, meta=meta
            )
            if id_col != "index":
                result = result.reset_index()
            result = result.rename(
                columns={"ds": time_col, "y": target_col, "unique_id": id_col}
            )
            results.append(result)

        return dd.concat(results)

# %% ../../nbs/distributed.forecast.ipynb 8
class DistributedForecast(DistributedMLForecast):
    def __init__(
        self,
        models: Models,
        freq: Optional[Freq] = None,
        lags: Optional[Lags] = None,
        lag_transforms: Optional[LagTransforms] = None,
        date_features: Optional[Iterable[DateFeature]] = None,
        differences: Optional[Differences] = None,
        num_threads: int = 1,
        client: Optional[Client] = None,
    ):
        warning_msg = (
            "The DistributedForecast class is deprecated and will be removed in a future version, "
            "please use the DistributedMLForecast class instead."
        )
        warnings.warn(warning_msg, DeprecationWarning)
        super().__init__(
            models,
            freq,
            lags,
            lag_transforms,
            date_features,
            differences,
            num_threads,
            client,
        )
