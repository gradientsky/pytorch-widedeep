"""
SharedEmbeddings is inspired by the TabTransformer available in AutoGluon:
https://github.com/awslabs/autogluon/tree/master/tabular/src/autogluon/tabular/models/tab_transformer
"""

import math
import warnings

import numpy as np
import torch
import einops
import torch.nn.functional as F
from torch import nn

from pytorch_widedeep.wdtypes import *  # noqa: F403
from pytorch_widedeep.models._get_activation_fn import get_activation_fn


class FullEmbeddingDropout(nn.Module):
    def __init__(self, p: float):
        super(FullEmbeddingDropout, self).__init__()

        if p < 0 or p > 1:
            raise ValueError(f"p probability has to be between 0 and 1, but got {p}")
        self.p = p

    def forward(self, X: Tensor) -> Tensor:
        if self.training:
            mask = X.new().resize_((X.size(1), 1)).bernoulli_(
                1 - self.dropout
            ).expand_as(X) / (1 - self.dropout)
            return mask * X
        else:
            return X

    def extra_repr(self) -> str:
        return f"p={self.p}"


DropoutLayers = Union[nn.Dropout, FullEmbeddingDropout]


class ContEmbeddings(nn.Module):
    def __init__(
        self,
        n_cont_cols: int,
        embed_dim: int,
        embed_dropout: float,
        use_bias: bool,
        activation: str = None,
    ):
        super(ContEmbeddings, self).__init__()

        self.n_cont_cols = n_cont_cols
        self.embed_dim = embed_dim
        self.embed_dropout = embed_dropout
        self.use_bias = use_bias
        self.activation = activation

        self.weight = nn.Parameter(torch.Tensor(n_cont_cols, embed_dim))

        self.bias = (
            nn.Parameter(torch.Tensor(n_cont_cols, embed_dim)) if use_bias else None
        )
        self._reset_parameters()

        self.act_fn = get_activation_fn(activation) if activation else None

    def _reset_parameters(self) -> None:
        nn.init.kaiming_uniform_(self.weight, a=math.sqrt(5))
        if self.bias is not None:
            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight)
            bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
            nn.init.uniform_(self.bias, -bound, bound)

    def forward(self, X: Tensor) -> Tensor:
        x = self.weight.unsqueeze(0) * X.unsqueeze(2)

        if self.bias is not None:
            x = x + self.bias.unsqueeze(0)

        if self.act_fn is not None:
            x = self.act_fn(x)

        return F.dropout(x, self.embed_dropout, self.training)

    def extra_repr(self) -> str:
        s = "{n_cont_cols}, {embed_dim}, embed_dropout={embed_dropout}, use_bias={use_bias}"
        if self.activation is not None:
            s += ", activation={activation}"
        return s.format(**self.__dict__)


class SharedEmbeddings(nn.Module):
    def __init__(
        self,
        n_embed: int,
        embed_dim: int,
        embed_dropout: float,
        full_embed_dropout: bool = False,
        add_shared_embed: bool = False,
        frac_shared_embed=0.25,
    ):
        super(SharedEmbeddings, self).__init__()

        assert frac_shared_embed < 1, "'frac_shared_embed' must be less than 1"
        self.add_shared_embed = add_shared_embed
        self.embed = nn.Embedding(n_embed, embed_dim, padding_idx=0)
        self.embed.weight.data.clamp_(-2, 2)
        if add_shared_embed:
            col_embed_dim = embed_dim
        else:
            col_embed_dim = int(embed_dim * frac_shared_embed)
        self.shared_embed = nn.Parameter(torch.empty(1, col_embed_dim).uniform_(-1, 1))

        if full_embed_dropout:
            self.dropout: DropoutLayers = FullEmbeddingDropout(embed_dropout)
        else:
            self.dropout = nn.Dropout(embed_dropout)

    def forward(self, X: Tensor) -> Tensor:
        out = self.dropout(self.embed(X))
        shared_embed = self.shared_embed.expand(out.shape[0], -1)
        if self.add_shared_embed:
            out += shared_embed
        else:
            out[:, : shared_embed.shape[1]] = shared_embed
        return out


class DiffSizeCatEmbeddings(nn.Module):
    def __init__(
        self,
        column_idx: Dict[str, int],
        embed_input: List[Tuple[str, int, int]],
        embed_dropout: float,
    ):
        super(DiffSizeCatEmbeddings, self).__init__()

        self.column_idx = column_idx
        self.embed_input = embed_input

        # Categorical: val + 1 because 0 is reserved for padding/unseen cateogories.
        self.embed_layers = nn.ModuleDict(
            {
                "emb_layer_" + col: nn.Embedding(val + 1, dim, padding_idx=0)
                for col, val, dim in self.embed_input
            }
        )
        self.embedding_dropout = nn.Dropout(embed_dropout)

        self.emb_out_dim: int = int(np.sum([embed[2] for embed in self.embed_input]))

    def forward(self, X: Tensor) -> Tensor:
        embed = [
            self.embed_layers["emb_layer_" + col](X[:, self.column_idx[col]].long())
            for col, _, _ in self.embed_input
        ]
        x = torch.cat(embed, 1)
        x = self.embedding_dropout(x)
        return x


class SameSizeCatEmbeddings(nn.Module):
    def __init__(
        self,
        embed_dim: int,
        column_idx: Dict[str, int],
        embed_input: Optional[List[Tuple[str, int]]],
        embed_dropout: float,
        full_embed_dropout: bool,
        shared_embed: bool,
        add_shared_embed: bool,
        frac_shared_embed: float,
        use_bias: bool,
    ):
        super(SameSizeCatEmbeddings, self).__init__()
        self.column_idx = column_idx
        self.embed_input = embed_input
        self.embed_dropout = embed_dropout
        self.shared_embed = shared_embed

        self.n_tokens = sum([ei[1] for ei in embed_input])
        self.categorical_cols = [ei[0] for ei in embed_input]
        self.cat_idx = [self.column_idx[col] for col in self.categorical_cols]

        if use_bias is not None:
            self.bias = nn.Parameter(
                torch.Tensor(len(self.categorical_cols), embed_dim)
            )
            nn.init.kaiming_uniform_(self.bias, a=math.sqrt(5))
            if shared_embed:
                warnings.warn(
                    "The current implementation of 'SharedEmbeddings' does not use bias",
                    UserWarning,
                )

        # Categorical: val + 1 because 0 is reserved for padding/unseen cateogories.
        if self.shared_embed:
            self.embed: Union[nn.ModuleDict, nn.Embedding] = nn.ModuleDict(
                {
                    "emb_layer_"
                    + col: SharedEmbeddings(
                        val if col == "cls_token" else val + 1,
                        embed_dim,
                        embed_dropout,
                        full_embed_dropout,
                        add_shared_embed,
                        frac_shared_embed,
                    )
                    for col, val in self.embed_input
                }
            )
        else:
            self.embed = nn.Embedding(self.n_tokens + 1, embed_dim, padding_idx=0)
            if full_embed_dropout:
                self.dropout: DropoutLayers = FullEmbeddingDropout(embed_dropout)
            else:
                self.dropout = nn.Dropout(embed_dropout)

    def forward(self, X: Tensor) -> Tensor:
        if self.shared_embed:
            cat_embed = [
                self.embed["emb_layer_" + col](  # type: ignore[index]
                    X[:, self.column_idx[col]].long()
                ).unsqueeze(1)
                for col, _ in self.embed_input
            ]
            x = torch.cat(cat_embed, 1)
        else:
            x = self.embed(X[:, self.cat_idx].long())
            if self.bias is not None:
                x = x + self.bias.unsqueeze(0)
            x = self.dropout(x)

        return x


class DiffSizeCatAndContEmbeddings(nn.Module):
    def __init__(
        self,
        column_idx: Dict[str, int],
        cat_embed_input: List[Tuple[str, int, int]],
        cat_embed_dropout: float,
        continuous_cols: Optional[List[str]],
        embed_continuous: bool,
        cont_embed_dim: int,
        cont_embed_dropout: float,
        cont_embed_activation: str,
        use_cont_bias: bool,
        cont_norm_layer: str,
    ):
        super(DiffSizeCatAndContEmbeddings, self).__init__()

        self.cat_embed_input = cat_embed_input
        self.continuous_cols = continuous_cols
        self.embed_continuous = embed_continuous

        # Categorical
        if self.cat_embed_input is not None:
            self.cat_embed = DiffSizeCatEmbeddings(
                column_idx, cat_embed_input, cat_embed_dropout
            )
            self.cat_out_dim = int(np.sum([embed[2] for embed in self.cat_embed_input]))
        else:
            self.cat_out_dim = 0

        # Continuous
        if continuous_cols is not None:
            self.cont_idx = [column_idx[col] for col in continuous_cols]
            if cont_norm_layer == "layernorm":
                self.cont_norm: NormLayers = nn.LayerNorm(len(continuous_cols))
            elif cont_norm_layer == "batchnorm":
                self.cont_norm = nn.BatchNorm1d(len(continuous_cols))
            else:
                self.cont_norm = nn.Identity()
            if self.embed_continuous:
                self.cont_embed = ContEmbeddings(
                    len(continuous_cols),
                    cont_embed_dim,
                    cont_embed_dropout,
                    use_cont_bias,
                    cont_embed_activation,
                )
                self.cont_out_dim = len(continuous_cols) * cont_embed_dim
            else:
                self.cont_out_dim = len(continuous_cols)
        else:
            self.cont_out_dim = 0

        self.output_dim = self.cat_out_dim + self.cont_out_dim

    def forward(self, X: Tensor) -> Tuple[Tensor, Any]:

        if self.cat_embed_input is not None:
            x_cat = self.cat_embed(X)
        else:
            x_cat = None

        if self.continuous_cols is not None:
            x_cont = self.cont_norm((X[:, self.cont_idx].float()))
            if self.embed_continuous:
                x_cont = self.cont_embed(x_cont)
                x_cont = einops.rearrange(x_cont, "b s d -> b (s d)")
        else:
            x_cont = None

        return x_cat, x_cont


class SameSizeCatAndContEmbeddings(nn.Module):
    def __init__(
        self,
        embed_dim: int,
        column_idx: Dict[str, int],
        cat_embed_input: Optional[List[Tuple[str, int]]],
        cat_embed_dropout: float,
        full_embed_dropout: bool,
        shared_embed: bool,
        add_shared_embed: bool,
        frac_shared_embed: float,
        use_embed_bias: bool,
        continuous_cols: Optional[List[str]],
        embed_continuous: bool,
        cont_embed_dropout: float,
        cont_embed_activation: str,
        use_cont_bias: bool,
        cont_norm_layer: str,
    ):
        super(SameSizeCatAndContEmbeddings, self).__init__()

        self.cat_embed_input = cat_embed_input
        self.continuous_cols = continuous_cols
        self.embed_continuous = embed_continuous

        # Categorical
        if cat_embed_input is not None:
            self.cat_embed = SameSizeCatEmbeddings(
                embed_dim,
                column_idx,
                cat_embed_input,
                cat_embed_dropout,
                full_embed_dropout,
                shared_embed,
                add_shared_embed,
                frac_shared_embed,
                use_embed_bias,
            )
        # Continuous
        if continuous_cols is not None:
            self.cont_idx = [column_idx[col] for col in continuous_cols]
            if cont_norm_layer == "layernorm":
                self.cont_norm: NormLayers = nn.LayerNorm(len(continuous_cols))
            elif cont_norm_layer == "batchnorm":
                self.cont_norm = nn.BatchNorm1d(len(continuous_cols))
            else:
                self.cont_norm = nn.Identity()
            if self.embed_continuous:
                self.cont_embed = ContEmbeddings(
                    len(continuous_cols),
                    embed_dim,
                    cont_embed_dropout,
                    use_cont_bias,
                    cont_embed_activation,
                )

    def forward(self, X: Tensor) -> Tuple[Tensor, Any]:

        if self.cat_embed_input is not None:
            x_cat = self.cat_embed(X)
        else:
            x_cat = None

        if self.continuous_cols is not None:
            x_cont = self.cont_norm((X[:, self.cont_idx].float()))
            if self.embed_continuous:
                x_cont = self.cont_embed(x_cont)
        else:
            x_cont = None

        return x_cat, x_cont
