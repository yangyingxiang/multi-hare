import torch.nn as nn
import torch.nn.functional as F
from modules.multi_dimensional_rnn import StateUpdateBlock
from torch.nn.modules.module import Module
import torch
from modules.inside_model_gradient_clipping import InsideModelGradientClamping


# This class optimizes the computation of multiple states computed
# using 1D convolutions that are computed from the same input, by
# computing them as a single convolution with more outputs, and then
# splitting the results.
# The states are computed as pairs, whereby the result of the second
# element of the pair needs to be shifted by one position.
# This is for the purpose of querying one original and one shifted element
# in MDLSTM computation: by shifting the result of the input convolution
# for the second hidden/memory state, all the other computations become
# simpler, since now the shifting has already been done so the same element
# from the original and shifted output can be combined, rather than explicitly
# shifting the querying index for getting the shifted result.
class ParallelMultipleStateWeightingsComputation(Module):
    def __init__(self, hidden_states_size: int,
                 number_of_paired_input_weightings: int,
                 output_states_size: int,
                 parallel_convolution: nn.Conv1d,
                 clamp_gradients: bool,
                 use_dropout: bool,
                 training: bool):
        super(ParallelMultipleStateWeightingsComputation, self).__init__()
        self.hidden_states_size = hidden_states_size
        self.number_of_paired_input_weightings = number_of_paired_input_weightings
        self.output_states_size = output_states_size
        self.parallel_convolution = parallel_convolution
        self.clamp_gradients = clamp_gradients
        self.use_dropout = use_dropout
        self.training = training

    @staticmethod
    def create_parallel_multiple_state_weighting_computation(hidden_states_size: int,
                                                             number_of_paired_input_weightings: int,
                                                             clamp_gradients: bool,
                                                             use_dropout: bool):
        output_states_size = hidden_states_size * number_of_paired_input_weightings * 2

        # parallel_convolution = nn.Conv1d(hidden_states_size, output_states_size, 1)

        parallel_convolution = nn.Conv1d(hidden_states_size, output_states_size, 1)

        # Xavier weight initialization
        torch.nn.init.xavier_uniform_(parallel_convolution.weight)

        return ParallelMultipleStateWeightingsComputation(hidden_states_size, number_of_paired_input_weightings,

                                                          output_states_size, parallel_convolution, clamp_gradients,
                                                          use_dropout,
                                                          True)

    # How to do dropout in pytorch:
    # https://discuss.pytorch.org/t/dropout-functional-api-advantages-disadvantages/181/4
    # https://github.com/pytorch/examples/blob/master/mnist/main.py
    # Where to apply dropout:
    # https://stats.stackexchange.com/questions/240305/where-should-i-place-dropout-layers-in-a-neural-network
    def compute_convolution_result(self, previous_state_column):
        if self.use_dropout:
                # print("Applying dropout...")
                # TODO: which probability to use for dropout?
                result = F.dropout(self.parallel_convolution(previous_state_column),   p=0.2, training=self.training)
                return result
        result = self.parallel_convolution(previous_state_column)

        if self.clamp_gradients:
            # print("ParallelMultipleStateWeightingsComputation - register gradient clamping...")
            # Create a 1d convolution with clamping of the gradient
            result = InsideModelGradientClamping.register_gradient_clamping_default_clamping_bound(result)

        return result

    def get_result_range_start_index(self, result_element_index):
        return self.hidden_states_size * result_element_index

    def get_result_range_end_index(self, result_element_index):
        return self.hidden_states_size * (result_element_index + 1)

    def compute_result_and_split_into_output_pairs(self, previous_state_column):
        result = list([])

        convolution_result = self.compute_convolution_result(previous_state_column)
        # print("convolution result: " + str(convolution_result))

        # print(">>> parallel_multiple_state_weightings_computation."
        #      + " compute_result_and_split_into_output_elements - convolution_result.size(): " +
        #      str(convolution_result.size()))

        for i in range(0, self.number_of_paired_input_weightings):
            range_begin = self.get_result_range_start_index(i * 2)
            range_end = self.get_result_range_end_index(i * 2)
            # print("range begin: " + str(range_begin) + " range end: " + str(range_end))
            pair_element_one = convolution_result[:, range_begin:range_end, :]
            range_begin = self.get_result_range_start_index(i * 2 + 1)
            range_end = self.get_result_range_end_index(i * 2 + 1)
            # print("range begin: " + str(range_begin) + " range end: " + str(range_end))
            pair_element_two = convolution_result[:, range_begin:range_end, :]
            pair = tuple((pair_element_one, pair_element_two))
            result.append(pair)
        return result

    def compute_result_and_split_into_pairs_with_second_pair_element_shifted(self, previous_state_column):
        result = list([])
        convolution_result_pairs = self.compute_result_and_split_into_output_pairs(previous_state_column)
        for result_pair in convolution_result_pairs:
            pair_element_one = result_pair[0]
            # pair_element_two = result_pair[1]
            # print("pair_element_two: " + str(pair_element_two))
            # The second pair element is shifted, so that the right elements are combined
            # for multi-dimensional RNN/LSTM computation

            # Slow
            # pair_two_element_shifted = StateUpdateBlock.get_shifted_column(result_pair[1], self.hidden_states_size)
            # summed_values = pair_element_one + pair_two_element_shifted

            # Faster
            pair_two_element_shifted = StateUpdateBlock.get_shifted_column_fast(result_pair[1])
            pair = tuple((pair_element_one, pair_two_element_shifted))
            result.append(pair)
        return result

    # This method :
    # 1. Computes the shared convolution over the previous_state_column
    # 2. Splits the output of the convolution with dimension of
    #    [batch_size, image_height, hidden_states_size * number_of_paired_input_weightings * 2]
    #    into pairs, each pair containing a part of the output of size
    #    [batch_size, image_height, hidden_states_size]
    #    the two pair elements correspond to a weighting of the first and second
    #    previous state respectively (to get the second previous state the results
    #    still need to be shifted by one position)
    # 3. Shift the second element of each pair one row down, and sum it with the first
    #    element to get the final output for each pair. Return the list of these
    #    results, which has number_of_paired_input_weightings elements
    def compute_summed_outputs_every_pair(self, previous_state_column):
        result = list([])
        convolution_result_pairs = self.\
            compute_result_and_split_into_pairs_with_second_pair_element_shifted(previous_state_column)
        for result_pair in convolution_result_pairs:
            pair_element_one = result_pair[0]
            pair_two_element_shifted = result_pair[1]
            # print("pair two element shifted: " + str(pair_two_element_shifted))
            summed_values = pair_element_one + pair_two_element_shifted
            result.append(summed_values)
        return result

    def get_state_convolutions_as_list(self):
        return list([self.parallel_convolution])

    # When testing the model, training should be set to false
    def set_training(self, training):
        self.training = training

    # This class extends Module so as to make sure that the parameters
    # are properly copied (to the right cuda device) when using nn.DataParallel(model)
    # and the to(device) method from  the Module base class
    # http://pytorch.org/docs/master/_modules/torch/nn/modules/module.html
    # The class is not however meant to be used as a stand-alone Module, so forward
    # is not implemented
    def forward(self, x):
        raise NotImplementedError
