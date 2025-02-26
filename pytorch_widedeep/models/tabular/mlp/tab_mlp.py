from pytorch_widedeep.wdtypes import *  # noqa: F403
from pytorch_widedeep.models.tabular.mlp._layers import MLP
from pytorch_widedeep.models.tabular._base_tabular_model import (
    BaseTabularModelWithoutAttention,
)


class TabMlp(BaseTabularModelWithoutAttention):
    r"""Defines a ``TabMlp`` model that can be used as the ``deeptabular``
    component of a Wide & Deep model or independently by itself.

    This class combines embedding representations of the categorical features
    with numerical (aka continuous) features, embedded or not. These are then
    passed through a series of dense layers (i.e. a MLP).

    Parameters
    ----------
    column_idx: Dict
        Dict containing the index of the columns that will be passed through
        the ``TabMlp`` model. Required to slice the tensors. e.g. {'education':
        0, 'relationship': 1, 'workclass': 2, ...}
    cat_embed_input: List, Optional, default = None
        List of Tuples with the column name, number of unique values and
        embedding dimension. e.g. [(education, 11, 32), ...]
    cat_embed_dropout: float, default = 0.1
        Categorical embeddings dropout
    use_cat_bias: bool, default = False,
        Boolean indicating if bias will be used for the categorical embeddings
    cat_embed_activation: Optional, str, default = None,
        Activation function for the categorical embeddings, if any. Currently
        `'tanh'`, `'relu'`, `'leaky_relu'` and `'gelu'` are supported
    continuous_cols: List, Optional, default = None
        List with the name of the numeric (aka continuous) columns
    cont_norm_layer: str, default =  "batchnorm"
        Type of normalization layer applied to the continuous features. Options
        are: 'layernorm', 'batchnorm' or None.
    embed_continuous: bool, default = False,
        Boolean indicating if the continuous columns will be embedded
        (i.e. passed each through a linear layer with or without activation)
    cont_embed_dim: int, default = 32,
        Size of the continuous embeddings
    cont_embed_dropout: float, default = 0.1,
        Dropout for the continuous embeddings
    use_cont_bias: bool, default = True,
        Boolean indicating if bias will be used for the continuous embeddings
    cont_embed_activation: Optional, str, default = None,
        Activation function for the continuous embeddings if any. Currently
        `'tanh'`, `'relu'`, `'leaky_relu'` and `'gelu'` are supported
    mlp_hidden_dims: List, default = [200, 100]
        List with the number of neurons per dense layer in the mlp.
    mlp_activation: str, default = "relu"
        Activation function for the dense layers of the MLP. Currently
        `'tanh'`, `'relu'`, `'leaky_relu'` and `'gelu'` are supported
    mlp_dropout: float or List, default = 0.1
        float or List of floats with the dropout between the dense layers.
        e.g: [0.5,0.5]
    mlp_batchnorm: bool, default = False
        Boolean indicating whether or not batch normalization will be applied
        to the dense layers
    mlp_batchnorm_last: bool, default = False
        Boolean indicating whether or not batch normalization will be applied
        to the last of the dense layers
    mlp_linear_first: bool, default = False
        Boolean indicating the order of the operations in the dense
        layer. If ``True: [LIN -> ACT -> BN -> DP]``. If ``False: [BN -> DP ->
        LIN -> ACT]``

    Attributes
    ----------
    cat_and_cont_embed: ``nn.Module``
        This is the module that processes the categorical and continuous columns
    tab_mlp: ``nn.Sequential``
        mlp model that will receive the concatenation of the embeddings and
        the continuous columns
    output_dim: int
        The output dimension of the model. This is a required attribute
        neccesary to build the ``WideDeep`` class

    Example
    --------
    >>> import torch
    >>> from pytorch_widedeep.models import TabMlp
    >>> X_tab = torch.cat((torch.empty(5, 4).random_(4), torch.rand(5, 1)), axis=1)
    >>> colnames = ['a', 'b', 'c', 'd', 'e']
    >>> cat_embed_input = [(u,i,j) for u,i,j in zip(colnames[:4], [4]*4, [8]*4)]
    >>> column_idx = {k:v for v,k in enumerate(colnames)}
    >>> model = TabMlp(mlp_hidden_dims=[8,4], column_idx=column_idx, cat_embed_input=cat_embed_input,
    ... continuous_cols = ['e'])
    >>> out = model(X_tab)
    """

    def __init__(
        self,
        column_idx: Dict[str, int],
        cat_embed_input: Optional[List[Tuple[str, int, int]]] = None,
        cat_embed_dropout: float = 0.1,
        use_cat_bias: bool = False,
        cat_embed_activation: Optional[str] = None,
        continuous_cols: Optional[List[str]] = None,
        cont_norm_layer: str = "batchnorm",
        embed_continuous: bool = False,
        cont_embed_dim: int = 32,
        cont_embed_dropout: float = 0.1,
        use_cont_bias: bool = True,
        cont_embed_activation: Optional[str] = None,
        mlp_hidden_dims: List[int] = [200, 100],
        mlp_activation: str = "relu",
        mlp_dropout: Union[float, List[float]] = 0.1,
        mlp_batchnorm: bool = False,
        mlp_batchnorm_last: bool = False,
        mlp_linear_first: bool = False,
    ):
        super(TabMlp, self).__init__(
            column_idx=column_idx,
            cat_embed_input=cat_embed_input,
            cat_embed_dropout=cat_embed_dropout,
            use_cat_bias=use_cat_bias,
            cat_embed_activation=cat_embed_activation,
            continuous_cols=continuous_cols,
            cont_norm_layer=cont_norm_layer,
            embed_continuous=embed_continuous,
            cont_embed_dim=cont_embed_dim,
            cont_embed_dropout=cont_embed_dropout,
            use_cont_bias=use_cont_bias,
            cont_embed_activation=cont_embed_activation,
        )

        self.mlp_hidden_dims = mlp_hidden_dims
        self.mlp_activation = mlp_activation
        self.mlp_dropout = mlp_dropout
        self.mlp_batchnorm = mlp_batchnorm
        self.mlp_batchnorm_last = mlp_batchnorm_last
        self.mlp_linear_first = mlp_linear_first

        # Embeddings are instantiated at the base model
        # Mlp
        mlp_input_dim = self.cat_and_cont_embed.output_dim
        mlp_hidden_dims = [mlp_input_dim] + mlp_hidden_dims
        self.tab_mlp = MLP(
            mlp_hidden_dims,
            mlp_activation,
            mlp_dropout,
            mlp_batchnorm,
            mlp_batchnorm_last,
            mlp_linear_first,
        )

        # the output_dim attribute will be used as input_dim when "merging" the models
        self.output_dim: int = mlp_hidden_dims[-1]

    def forward(self, X: Tensor) -> Tensor:
        x = self._get_embeddings(X)
        return self.tab_mlp(x)
