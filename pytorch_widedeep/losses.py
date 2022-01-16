import torch
import torch.nn as nn
import torch.nn.functional as F

from pytorch_widedeep.wdtypes import *  # noqa: F403

use_cuda = torch.cuda.is_available()


class TweedieLoss(nn.Module):
    """
    Tweedie loss for extremely unbalanced zero-inflated data``
    All credits go to `Wenbo Shi
    <https://towardsdatascience.com/tweedie-loss-function-for-right-skewed-data-2c5ca470678f> and
    <https://arxiv.org/abs/1811.10192>`
    """

    def __init__(self):
        super().__init__()

    def forward(
        self,
        input: Tensor,
        target: Tensor,
        lds_weight: Union[None, Tensor] = None,
        p=1.5,
    ) -> Tensor:
        assert (
            input.min() > 0
        ), """All input values must be >=0, if your model is predicting
            values <0 try to enforce positive values by activation function
            on last layer with `trainer.enforce_positive_output=True`"""
        assert target.min() >= 0, "All target values must be >=0"
        loss = -target * torch.pow(input, 1 - p) / (1 - p) + torch.pow(input, 2 - p) / (
            2 - p
        )
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)

        return torch.mean(loss)


class QuantileLoss(nn.Module):
    r"""Quantile loss defined as:

        :math:`Loss = max(q \times (y-y_{pred}), (1-q) \times (y_{pred}-y))`

    All credits go to the implementation at `pytorch-forecasting
    <https://pytorch-forecasting.readthedocs.io/en/latest/_modules/pytorch_forecasting/metrics.html#QuantileLoss>`_ .

    Parameters
    ----------
    quantiles: List, default = [0.02, 0.1, 0.25, 0.5, 0.75, 0.9, 0.98]
        List of quantiles
    """

    def __init__(
        self,
        quantiles: List[float] = [0.02, 0.1, 0.25, 0.5, 0.75, 0.9, 0.98],
    ):
        super().__init__()
        self.quantiles = quantiles

    def forward(self, input: Tensor, target: Tensor) -> Tensor:

        assert input.shape == torch.Size([target.shape[0], len(self.quantiles)]), (
            f"Wrong shape of input, pred_dim of the model that is using QuantileLoss must be equal "
            f"to number of quantiles, i.e. {len(self.quantiles)}."
        )
        target = target.view(-1, 1).float()
        losses = []
        for i, q in enumerate(self.quantiles):
            errors = target - input[..., i]
            losses.append(torch.max((q - 1) * errors, q * errors).unsqueeze(-1))

        loss = torch.cat(losses, dim=2)

        return torch.mean(loss)


class ZILNLoss(nn.Module):
    r"""Adjusted implementation of the `Zero Inflated LogNormal loss
    <https://arxiv.org/pdf/1912.07753.pdf>` and its `code
    <https://github.com/google/lifetime_value/blob/master/lifetime_value/zero_inflated_lognormal.py>`
    """

    def __init__(self):
        super().__init__()

    def forward(self, input: Tensor, target: Tensor) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities) with spape (N,3), where N is the batch size
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        >>> import torch
        >>>
        >>> from pytorch_widedeep.losses import ZILNLoss
        >>>
        >>> # REGRESSION
        >>> target = torch.tensor([[0., 1.5]]).view(-1, 1)
        >>> input = torch.tensor([[.1, .2, .3], [.4, .5, .6]])
        >>> ZILNLoss()(input, target)
        tensor(1.3114)
        """
        positive = target > 0
        positive = positive.float()

        assert input.shape == torch.Size(
            [target.shape[0], 3]
        ), "Wrong shape of input, pred_dim of the model that is using ZILNLoss must be equal to 3."

        positive_input = input[..., :1]

        classification_loss = F.binary_cross_entropy_with_logits(
            positive_input, positive, reduction="none"
        ).flatten()

        loc = input[..., 1:2]

        # when using max the two input tensors (input and other) have to be of
        # the same type
        max_input = F.softplus(input[..., 2:])
        max_other = torch.sqrt(torch.Tensor([torch.finfo(torch.double).eps])).type(
            max_input.type()
        )
        scale = torch.max(max_input, max_other)
        safe_labels = positive * target + (1 - positive) * torch.ones_like(target)

        regression_loss = -torch.mean(
            positive
            * torch.distributions.log_normal.LogNormal(loc=loc, scale=scale).log_prob(
                safe_labels
            ),
            dim=-1,
        )

        return torch.mean(classification_loss + regression_loss)


class FocalLoss(nn.Module):
    r"""Implementation of the `focal loss
    <https://arxiv.org/pdf/1708.02002.pdf>`_ for both binary and
    multiclass classification

    :math:`FL(p_t) = \alpha (1 - p_t)^{\gamma} log(p_t)`

    where, for a case of a binary classification problem

    :math:`\begin{equation} p_t= \begin{cases}p, & \text{if $y=1$}.\\1-p, & \text{otherwise}. \end{cases} \end{equation}`

    Parameters
    ----------
    alpha: float
        Focal Loss ``alpha`` parameter
    gamma: float
        Focal Loss ``gamma`` parameter
    """

    def __init__(self, alpha: float = 0.25, gamma: float = 1.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def _get_weight(self, p: Tensor, t: Tensor) -> Tensor:
        pt = p * t + (1 - p) * (1 - t)  # type: ignore
        w = self.alpha * t + (1 - self.alpha) * (1 - t)  # type: ignore
        return (w * (1 - pt).pow(self.gamma)).detach()  # type: ignore

    def forward(self, input: Tensor, target: Tensor) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        >>> import torch
        >>>
        >>> from pytorch_widedeep.losses import FocalLoss
        >>>
        >>> # BINARY
        >>> target = torch.tensor([0, 1, 0, 1]).view(-1, 1)
        >>> input = torch.tensor([[0.6, 0.7, 0.3, 0.8]]).t()
        >>> FocalLoss()(input, target)
        tensor(0.1762)
        >>>
        >>> # MULTICLASS
        >>> target = torch.tensor([1, 0, 2]).view(-1, 1)
        >>> input = torch.tensor([[0.2, 0.5, 0.3], [0.8, 0.1, 0.1], [0.7, 0.2, 0.1]])
        >>> FocalLoss()(input, target)
        tensor(0.2573)
        """
        input_prob = torch.sigmoid(input)
        if input.size(1) == 1:
            input_prob = torch.cat([1 - input_prob, input_prob], axis=1)  # type: ignore
            num_class = 2
        else:
            num_class = input_prob.size(1)
        binary_target = torch.eye(num_class)[target.squeeze().long()]
        if use_cuda:
            binary_target = binary_target.cuda()
        binary_target = binary_target.contiguous()
        weight = self._get_weight(input_prob, binary_target)

        return F.binary_cross_entropy(
            input_prob, binary_target, weight, reduction="mean"
        )


class L1Loss(nn.Module):
    r"""Based on
    `Yang, Y., Zha, K., Chen, Y. C., Wang, H., & Katabi, D. (2021).
    Delving into Deep Imbalanced Regression. arXiv preprint arXiv:2102.09554.`
    and their `implementation
    <https://github.com/YyzHarry/imbalanced-regression>`
    """

    def __init__(self):
        super().__init__()

    def forward(
        self, input: Tensor, target: Tensor, lds_weight: Union[None, Tensor] = None
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        """
        loss = F.l1_loss(input, target, reduction="none")
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)

        return loss


class MSELoss(nn.Module):
    r"""Based on
    `Yang, Y., Zha, K., Chen, Y. C., Wang, H., & Katabi, D. (2021).
    Delving into Deep Imbalanced Regression. arXiv preprint arXiv:2102.09554.`
    and their `implementation
    <https://github.com/YyzHarry/imbalanced-regression>`
    """

    def __init__(self):
        super().__init__()

    def forward(
        self, input: Tensor, target: Tensor, lds_weight: Union[None, Tensor] = None
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        """
        loss = (input - target) ** 2
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)

        return loss


class MSLELoss(nn.Module):
    r"""mean squared log error"""

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self, input: Tensor, target: Tensor, lds_weight: Union[None, Tensor] = None
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        >>> import torch
        >>> from pytorch_widedeep.losses import MSLELoss
        >>>
        >>> target = torch.tensor([1, 1.2, 0, 2]).view(-1, 1)
        >>> input = torch.tensor([0.6, 0.7, 0.3, 0.8]).view(-1, 1)
        >>> MSLELoss()(input, target)
        tensor(0.1115)
        """
        assert (
            input.min() >= 0
        ), """All input values must be >=0, if your model is predicting
            values <0 try to enforce positive values by activation function
            on last layer with `trainer.enforce_positive_output=True`"""
        assert target.min() >= 0, "All target values must be >=0"

        loss = (torch.log(input + 1) - torch.log(target + 1)) ** 2
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)

        return loss


class RMSELoss(nn.Module):
    r"""root mean squared error"""

    def __init__(self):
        super().__init__()

    def forward(
        self, input: Tensor, target: Tensor, lds_weight: Union[None, Tensor] = None
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        >>> import torch
        >>> from pytorch_widedeep.losses import RMSELoss
        >>>
        >>> target = torch.tensor([1, 1.2, 0, 2]).view(-1, 1)
        >>> input = torch.tensor([0.6, 0.7, 0.3, 0.8]).view(-1, 1)
        >>> RMSELoss()(input, target)
        tensor(0.6964)
        """
        loss = (input - target) ** 2
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)
        loss = torch.sqrt(loss)

        return loss


class RMSLELoss(nn.Module):
    r"""root mean squared log error"""

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self, input: Tensor, target: Tensor, lds_weight: Union[None, Tensor] = None
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        >>> import torch
        >>> from pytorch_widedeep.losses import RMSLELoss
        >>>
        >>> target = torch.tensor([1, 1.2, 0, 2]).view(-1, 1)
        >>> input = torch.tensor([0.6, 0.7, 0.3, 0.8]).view(-1, 1)
        >>> RMSLELoss()(input, target)
        tensor(0.3339)
        """
        assert (
            input.min() >= 0
        ), """All input values must be >=0, if your model is predicting
            values <0 try to enforce positive values by activation function
            on last layer with `trainer.enforce_positive_output=True`"""
        assert target.min() >= 0, "All target values must be >=0"

        loss = (torch.log(input + 1) - torch.log(target + 1)) ** 2
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)
        loss = torch.sqrt(loss)

        return loss


class FocalR_L1Loss(nn.Module):
    r"""Focal-R L1 loss based on :

    * `Yang, Y., Zha, K., Chen, Y. C., Wang, H., & Katabi, D. (2021).
    Delving into Deep Imbalanced Regression. arXiv preprint arXiv:2102.09554.`
    * their `implementation:
    <https://github.com/YyzHarry/imbalanced-regression>`
    * additional explanation to 2*sigmoid(..)-1:
    https://github.com/YyzHarry/imbalanced-regression/issues/16
    """

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self,
        input: Tensor,
        target: Tensor,
        lds_weight: Union[None, Tensor] = None,
        activate: Literal["sigmoid", "tanh"] = "sigmoid",
        beta=0.2,
        gamma=1,
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        """
        loss = F.l1_loss(input, target, reduction="none")
        if activate == "tanh":
            loss *= (torch.tanh(beta * torch.abs(input - target))) ** gamma
        else:
            loss *= (2 * torch.sigmoid(beta * torch.abs(input - target)) - 1) ** gamma
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)

        return loss


class FocalR_MSELoss(nn.Module):
    r"""Focal-R MSE loss based on :

    * `Yang, Y., Zha, K., Chen, Y. C., Wang, H., & Katabi, D. (2021).
    Delving into Deep Imbalanced Regression. arXiv preprint arXiv:2102.09554.`
    * their `implementation:
    <https://github.com/YyzHarry/imbalanced-regression>`
    * additional explanation to 2*sigmoid(..)-1:
    https://github.com/YyzHarry/imbalanced-regression/issues/16
    """

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self,
        input: Tensor,
        target: Tensor,
        lds_weight: Union[None, Tensor] = None,
        activate: Literal["sigmoid", "tanh"] = "sigmoid",
        beta=0.2,
        gamma=1,
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        """
        loss = (input - target) ** 2
        if activate == "tanh":
            loss *= (torch.tanh(beta * torch.abs(input - target))) ** gamma
        else:
            loss *= (
                2 * torch.sigmoid(beta * torch.abs((input - target) ** 2)) - 1
            ) ** gamma
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)

        return loss


class FocalR_RMSELoss(nn.Module):
    r"""Focal-R RMSE loss based on :

    * `Yang, Y., Zha, K., Chen, Y. C., Wang, H., & Katabi, D. (2021).
    Delving into Deep Imbalanced Regression. arXiv preprint arXiv:2102.09554.`
    * their `implementation:
    <https://github.com/YyzHarry/imbalanced-regression>`
    * additional explanation to 2*sigmoid(..)-1:
    https://github.com/YyzHarry/imbalanced-regression/issues/16
    """

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self,
        input: Tensor,
        target: Tensor,
        lds_weight: Union[None, Tensor] = None,
        activate: Literal["sigmoid", "tanh"] = "sigmoid",
        beta=0.2,
        gamma=1,
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        """
        loss = (input - target) ** 2
        if activate == "tanh":
            loss *= (torch.tanh(beta * torch.abs(input - target))) ** gamma
        else:
            loss *= (
                2 * torch.sigmoid(beta * torch.abs((input - target) ** 2)) - 1
            ) ** gamma
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)
        loss = torch.sqrt(loss)

        return loss


class HuberLoss(nn.Module):
    r"""Based on
    `Yang, Y., Zha, K., Chen, Y. C., Wang, H., & Katabi, D. (2021).
    Delving into Deep Imbalanced Regression. arXiv preprint arXiv:2102.09554.`
    and their `implementation
    <https://github.com/YyzHarry/imbalanced-regression>`
    """

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self,
        input: Tensor,
        target: Tensor,
        lds_weight: Union[None, Tensor] = None,
        beta=0.1,
    ) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes

        Examples
        --------
        """
        l1_loss = torch.abs(input - target)
        cond = l1_loss < beta
        loss = torch.where(cond, 0.5 * l1_loss ** 2 / beta, l1_loss - 0.5 * beta)
        if lds_weight is not None:
            loss *= lds_weight.expand_as(loss)
        loss = torch.mean(loss)

        return loss


class BayesianRegressionLoss(nn.Module):
    r"""log Gaussian loss as specified in the original publication 'Weight
    Uncertainty in Neural Networks'
    Currently we do not use this loss as is proportional to the
    ``BayesianSELoss`` and the latter does not need a scale/noise_tolerance
    param
    """

    def __init__(self, noise_tolerance: float):
        super().__init__()
        self.noise_tolerance = noise_tolerance

    def forward(self, input: Tensor, target: Tensor) -> Tensor:
        return (
            -torch.distributions.Normal(input, self.noise_tolerance)
            .log_prob(target)
            .sum()
        )


class BayesianSELoss(nn.Module):
    r"""Squared Loss (log Gaussian) for the case of a regression as specified in
    the original publication 'Weight Uncertainty in Neural Networks'
    """

    def __init__(self):
        super().__init__()

    def forward(self, input: Tensor, target: Tensor) -> Tensor:
        r"""
        Parameters
        ----------
        input: Tensor
            input tensor with predictions (not probabilities)
        target: Tensor
            target tensor with the actual classes
        Examples
        --------
        >>> import torch
        >>> from pytorch_widedeep.losses import BayesianSELoss
        >>> target = torch.tensor([1, 1.2, 0, 2]).view(-1, 1)
        >>> input = torch.tensor([0.6, 0.7, 0.3, 0.8]).view(-1, 1)
        >>> BayesianSELoss()(input, target)
        tensor(0.9700)
        """
        return (0.5 * (input - target) ** 2).sum()
