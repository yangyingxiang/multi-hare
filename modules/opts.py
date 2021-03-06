import argparse

__author__ = "Dublin City University"
__copyright__ = "Copyright 2019, Dublin City University"
__credits__ = ["Gideon Maillette de Buy Wenniger"]
__license__ = "Dublin City University Software License (enclosed)"


# Model options, adapted from opnennmt model options
def model_opts(parser):
    """
    These options are passed to the construction of the model.
    Be careful with these as they will be used during handwriting recognition.
    """

    # Encoder-Deocder Options
    group = parser.add_argument_group('Model- Encoder-Decoder')
    # group.add_argument('-model_type', default='text',
    #                    help="""Type of source model to use. Allows
    #                    the system to incorporate non-text inputs.
    #                    Options are [text|img|audio].""")

    group.add_argument('-layer_pairs', type=int, default=-1,
                       help='Number of {BlockMDLSTM,BlockConvolution} layer pairs.')
    group.add_argument('-first_layer_hidden_states_size', type=int, default=8,
                       help='Size of hidden states in the first layer. This is only used when '
                            '-use_network_structure_bluche is not used, otherwise '
                            '-mdlstm_layer_sizes is used to set the layer sizes')

    # We want a boolean flag that is required, but that is allowed to be either true or false
    # The way below works to do this
    # See: https://stackoverflow.com/questions/15008758/parsing-boolean-values-with-argparse
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_bias_in_block_strided_convolution', dest='use_bias_in_block_strided_convolution',
                       action='store_true')
    group.add_argument('-no_bias_in_block_strided_convolution', dest='use_bias_in_block_strided_convolution',
                       action='store_false')
    # parser.set_defaults(feature=False)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_block_mdlstm_layers', dest='use_block_mdlstm',
                       action='store_true')
    group.add_argument('-use_regular_mdlstm_layers', dest='use_block_mdlstm',
                       action='store_false')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_example_packing', dest='use_example_packing',
                       help="""Whether or not to use example packing for more efficient 
                       computation.""",
                       action='store_true')
    group.add_argument('-no_example_packing', dest='use_example_packing',
                       help="""Whether or not to use example packing for more efficient 
                           computation.""",
                       action='store_false')


def preprocess_opts(parser):
    # Data options
    group = parser.add_argument_group('Data')

    # Data processing options
    group = parser.add_argument_group('Random')
    group.add_argument('-shuffle', type=int, default=1,
                       help="Shuffle data")
    group.add_argument('-seed', type=int, default=3435,
                       help="Random seed")

    group = parser.add_argument_group('Logging')
    group.add_argument('-report_every', type=int, default=100000,
                       help="Report status every this many sentences")


def train_opts(parser):
    # Model loading/saving options

    group = parser.add_argument_group('General')
    # group.add_argument('-data', required=True,
    #                    help="""Path prefix to the ".train.pt" and
    #                    ".valid.pt" file path from preprocess.py""")

    group.add_argument('-iam_database_line_images_root_folder_path',
                       help="Path to the IAM database line images root folder",
                       required=True)

    group.add_argument('-iam_database_lines_file_path',
                       help="Path to the IAM database (ascii) lines file",
                       required=True)

    group.add_argument('-examples_database_data_type',
                       type=str,
                       help="The data type to train and test for: "
                            "variable_length_mnist: an artificial dataset of MNIST "
                            "character sequences of variable length. Mainly used for development/debugging."
                            "rimes_lines: the rimes lines dataset"
                            "iam_words: the iam words dataset"
                            "iam_ lines: the iam lines dataset",
                       choices=["variable_length_mnist", "rimes_lines", "iam_words", "iam_lines"],
                       required=True)

    group.add_argument('-data_permutation_file_path', type=str,
                       help="""Path to the data permutation file for saving or 
                            loading from. This is used to keep the data order
                             fixed between experiments, or when loading from a
                             checkpoint""",
                       required=True)

    group.add_argument('-vocabulary_file_path', type=str,
                       help="""Path to the vocabulary file for saving or 
                                loading from. This is used to keep the vocabulary the 
                                same, even when for example the order of the lines in the file 
                                with the training data changes in between saving to and 
                                loading from a checkpoint""",
                       required=True)

    group.add_argument('-save_model', default='model',
                       help="""Model filename (the model will be saved as
                       <save_model>_epochN_accuracy.pt where PPL is the
                       validation accuracy""")

    group.add_argument("-save_score_table_file_path", type=str,
                       help="path to the file used for saving the development scores in a table format",
                       required=True)

    group.add_argument("-save_dev_set_file_path", type=str,
                       help="path used to save the dev-set to",
                       default=None)

    group.add_argument("-save_test_set_file_path", type=str,
                       help="path used to save the dev-set to",
                       default=None)

    group.add_argument("-dataset_save_or_load_file_path", type=str,
                       help="path used to save the dataset to",
                       default=None, required=True)


    group.add_argument('-block_strided_convolution_layers_using_weight_sharing', default=[], nargs='+', type=int,
                       help="Use weight-sharing across directions for one or more block-strided convolution"
                            "layers. The default is no weight-sharing, use for example : "
                            "\" -block_strided_convolution_layers_using_weight_sharing 0 1 \" to use weight-sharing "
                            "in the first and second block-strided convolution layer, or "
                            "\"-block_strided_convolution_layers_using_weight_sharing 1\" to use it only in the second "
                            "layer")

    # GPU
    group.add_argument('-gpuid', default=[], nargs='+', type=int,
                       help="Use CUDA on the listed devices.")

    group.add_argument('-seed', type=int, default=-1,
                       help="""Random seed used for the experiments
                       reproducibility.""")

    # Reset the adam optimizer state, and learning rate
    group.add_argument('-reset_adam_state',
                       dest='reset_adam_state',
                       action='store_true',
                       help="Using this flag, a manual reset of the adam state can be forced")

    # Number of MDLSTM nodes for layers
    group.add_argument('-mdlstm_layer_sizes', default=[], nargs='+', type=int, required=True,
                       help="A list specifying the sizes of the MDLSTM (or Leak-LP) cell layers."
                            "For examples \"-mdlstm_layer_sizes 2 10 50\"")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_four_pixel_input_blocks', dest='use_four_pixel_input_blocks',
                       action='store_true')
    group.add_argument('-use_resolution_halving', dest='use_four_pixel_input_blocks',
                       action='store_false')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_leaky_lp_cells', dest='use_leaky_lp_cells',
                       action='store_true')
    group.add_argument('-use_normal_mdlstm_cells', dest='use_leaky_lp_cells',
                       action='store_false')


    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_on_demand_example_loading', dest='use_on_demand_example_loading',
                       help="""Use on-demand example loading, 
                       which saves all pre-processed examples individually to disk 
                       and loads them on the fly as needed, saving memory. This setting is essential
                       when the entire dataset does not fit properly in working memory. 
                       """,
                       action='store_true')
    group.add_argument('-load_entire_dataset_beforehand', dest='use_on_demand_example_loading',
                       help="""Load the entire dataset beforehand rather than loading 
                       individual example on demand. Use this only when you have enough memory.""",
                       action='store_false')



    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_network_structure_bluche', dest='use_network_structure_bluche',
                       action='store_true', help="The Bluche (Pham. et.al, 2014) network structure " +
                       "consists of two MDLSTM + block-strided convolution layer pairs and one " +
                       "third MDLSTM layer, followed by four fully directed layers, one for each " +
                       "direction, of which the output is summed")
    group.add_argument('-use_only_complete_mdlstm_plus_block_strided_convolution_layer_pairs',
                       dest='use_network_structure_bluche',
                       action='store_false', help="With this option set, multiple full MDLSTM plus" +
                       "block-strided convolution layer pairs are used, with the last " +
                       "block-strided convolution combining the directions, and providing its output" +
                       "to a single fully connected layer")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-share_weights_across_directions_in_fully_connected_layer',
                       dest='share_weights_across_directions_in_fully_connected_layer',
                       action='store_true',
                       help="Share the weights in the last fully connected layer that combines the output of "
                            "four MDLSTMs. This switch only has effect if the one-but-last layer is an MDLSTM layer "
                            "and not if it is a block-strided convolution layer.")
    group.add_argument('-use_unique_weights_for_each_directions_in_fully_connected_layer',
                       dest='share_weights_across_directions_in_fully_connected_layer',
                       action='store_false',
                       help="Do not share the weights in the last fully connected layer that combines the output of "
                            "four MDLSTMs, but have (conceptually) four different layers, "
                            "one for each direction, whose "
                            "output are summed. In the actual implementation there is just one layer, with four times" 
                            " as many inputs, which is the same thing, except for removing redundant bias weights."
                            " This switch only has effect if the one-but-last layer is an MDLSTM layer "
                            "and not if it is a block-strided convolution layer.")

    # Init options
    group = parser.add_argument_group('Initialization')
    group.add_argument('-param_init', type=float, default=0.1,
                       help="""Parameters are initialized over uniform distribution
                       with support (-param_init, param_init).
                       Use 0 to not use initialization""")
    group.add_argument('-param_init_glorot', action='store_true',
                       help="""Init parameters with xavier_uniform.
                       Required for MDLSTM.""")

    group.add_argument('-train_from', default='', type=str,
                       help="""If training from a checkpoint then this is the
                       path to the pretrained model's state_dict.""")

    # Language model options
    group = parser.add_argument_group('language-model')
    group.add_argument('-language_model_file_path', type=str,
                       help="Path to (binary) kenlm language model file",
                       required=True)
    group.add_argument('-language_model_weight', type=float,
                       help='The weight of the language model (also known as '
                            'the decoder parameter \"alpha\")', required=True)
    group.add_argument('-word_insertion_penalty', type=float,
                       help='The word insertion penalty (also known as '
                            'the decoder parameter \"beta\")', required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_language_model', dest='use_language_model',
                       action='store_true')
    group.add_argument('-no_language_model', dest='use_language_model',
                       action='store_false')

    # Data split options
    group = parser.add_argument_group('data-split')
    group.add_argument('-train_split_file_path', type=str,
                       help="Path to file specifying the train split",
                       required=False)
    group.add_argument('-dev_split_file_path', type=str,
                       help="Path to file specifying the dev split",
                       required=False)
    group.add_argument('-test_split_file_path', type=str,
                       help="Path to file specifying the dev split",
                       required=False)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_split_files_specified_data_split', dest='use_split_files_specified_data_split',
                       action='store_true')
    group.add_argument('-use_fractions_based_data_split', dest='use_split_files_specified_data_split',
                       action='store_false')

    # Optimization options
    group = parser.add_argument_group('Optimization- Type')
    group.add_argument('-batch_size', type=int, default=64,
                       help='Maximum batch size for training')
    group.add_argument('-valid_batch_size', type=int, default=32,
                       help='Maximum batch size for validation')
    group.add_argument('-epochs', type=int, default=80,
                       help='Number of training epochs')
    group.add_argument('-optim', default='sgd',
                       choices=['sgd', 'adagrad', 'adadelta', 'adam',
                                'sparseadam'],
                       help="""Optimization method.""")
    group.add_argument('-adagrad_accumulator_init', type=float, default=0,
                       help="""Initializes the accumulator values in adagrad.
                       Mirrors the initial_accumulator_value option
                       in the tensorflow adagrad (use 0.1 for their default).
                       """)
    group.add_argument('-max_grad_norm', type=float, default=10,
                       help="""If the norm of the gradient vector exceeds this,
                       renormalize it to have the norm equal to
                       max_grad_norm""")
    group.add_argument('-adam_beta1', type=float, default=0.9,
                       help="""The beta1 parameter used by Adam.
                       Almost without exception a value of 0.9 is used in
                       the literature, seemingly giving good results,
                       so we would discourage changing this value from
                       the default without due consideration.""")
    group.add_argument('-adam_beta2', type=float, default=0.999,
                       help="""The beta2 parameter used by Adam.
                       Typically a value of 0.999 is recommended, as this is
                       the value suggested by the original paper describing
                       Adam, and is also the value adopted in other frameworks
                       such as Tensorflow and Kerras, i.e. see:
                       https://www.tensorflow.org/api_docs/python/tf/train/AdamOptimizer
                       https://keras.io/optimizers/ .
                       Whereas recently the paper "Attention is All You Need"
                       suggested a value of 0.98 for beta2, this parameter may
                       not work well for normal models / default
                       baselines.""")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-use_dropout', dest='use_dropout',
                       help="""Whether or not to use dropout.""",
                       action='store_true')
    group.add_argument('-no_dropout', dest='use_dropout',
                       help="""Whether or not to use dropout.""",
                       action='store_false')
    # learning rate
    group = parser.add_argument_group('Optimization- Rate')
    group.add_argument('-learning_rate', type=float, default=1.0,
                       required=True,
                       help="""Starting learning rate.
                       Recommended settings: sgd = 1, adagrad = 0.1,
                       adadelta = 1, adam = 0.001""")
    group.add_argument('-learning_rate_decay', type=float, default=0.5,
                       help="""If update_learning_rate, decay learning rate by
                       scaling it with this factor if epoch has gone past
                       start_decay_at""")
    group.add_argument('-start_decay_at', type=int, default=8,
                       help="""Start decaying every epoch after and including this
                       epoch""")
    group.add_argument('-start_checkpoint_at', type=int, default=0,
                       help="""Start checkpointing every epoch after and including
                       this epoch""")
    group.add_argument('-decay_method', type=str, default="",
                       choices=['noam'], help="Use a custom decay rate.")
    group.add_argument('-warmup_steps', type=int, default=4000,
                       help="""Number of warmup steps for custom decay.""")

    group = parser.add_argument_group('Logging')
    group.add_argument('-report_every', type=int, default=50,
                       help="Print stats at this interval.")
    group.add_argument('-exp', type=str, default="",
                       help="Name of the experiment for logging.")


def decode_opts(parser):
    group = parser.add_argument_group('Model')
    group.add_argument('-model', required=True,
                       help='Path to model .pt file')


def add_md_help_argument(parser):
    parser.add_argument('-md', action=MarkdownHelpAction,
                        help='print Markdown-formatted help text and exit.')


# MARKDOWN boilerplate

# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
class MarkdownHelpFormatter(argparse.HelpFormatter):
    """A really bare-bones argparse help formatter that generates valid markdown.
    This will generate something like:
    usage
    # **section heading**:
    ## **--argument-one**
    ```
    argument-one help text
    ```
    """

    def _format_usage(self, usage, actions, groups, prefix):
        return ""

    def format_help(self):
        print(self._prog)
        self._root_section.heading = '# Options: %s' % self._prog
        return super(MarkdownHelpFormatter, self).format_help()

    def start_section(self, heading):
        super(MarkdownHelpFormatter, self)\
            .start_section('### **%s**' % heading)

    def _format_action(self, action):
        if action.dest == "help" or action.dest == "md":
            return ""
        lines = []
        lines.append('* **-%s %s** ' % (action.dest,
                                        "[%s]" % action.default
                                        if action.default else "[]"))
        if action.help:
            help_text = self._expand_help(action)
            lines.extend(self._split_lines(help_text, 80))
        lines.extend(['', ''])
        return '\n'.join(lines)


class MarkdownHelpAction(argparse.Action):
    def __init__(self, option_strings,
                 dest=argparse.SUPPRESS, default=argparse.SUPPRESS,
                 **kwargs):
        super(MarkdownHelpAction, self).__init__(
            option_strings=option_strings,
            dest=dest,
            default=default,
            nargs=0,
            **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        parser.formatter_class = MarkdownHelpFormatter
        parser.print_help()
        parser.exit()


class DeprecateAction(argparse.Action):
    def __init__(self, option_strings, dest, help=None, **kwargs):
        super(DeprecateAction, self).__init__(option_strings, dest, nargs=0,
                                              help=help, **kwargs)

    def __call__(self, parser, namespace, values, flag_name):
        help = self.help if self.help is not None else ""
        msg = "Flag '%s' is deprecated. %s" % (flag_name, help)
        raise argparse.ArgumentTypeError(msg)
