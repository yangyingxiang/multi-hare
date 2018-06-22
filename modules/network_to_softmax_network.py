import torch
import torch.nn as nn
from modules.size_two_dimensional import SizeTwoDimensional
from abc import abstractmethod
from modules.inside_model_gradient_clipping import InsideModelGradientClamping

class ActivationsResizer:

    def __init__(self, network):
        self.network = network

    @abstractmethod
    def create_resized_activations(self, activations):
        raise RuntimeError("not implemented")

    @abstractmethod
    def get_number_of_output_channels(self):
        raise RuntimeError("not implemented")


class SumActivationsResizer(ActivationsResizer):

    def __init__(self, network):
        super(SumActivationsResizer, self).__init__(network)

    def create_resized_activations(self, activations):
        activations_height = activations.size(2)

        # It can be that the activations dimensionality is not exactly 1, in which case it is reduced to 1
        # by simple summation
        if activations_height != 1:
            # raise RuntimeError("Error: the height dimension of returned activations should be of size 1, but it " +
            #                   "was: " + str(activations.size(2)))
            # print("WARNING: activations height is " + str(activations_height) + " ( > 1) :\n" +
            #      "Converting to a height of 1 by summing over the rows")
            activations = torch.sum(activations, dim=2)
            # activations = activations[:, :, 0, :] # Wrong and not really faster
        return activations

    @abstractmethod
    def get_number_of_output_channels(self):
        return self.network.get_number_of_output_channels()


# This activations resizer concatenates the activation rows along the
# channels dimension, rather than summing them, keeping all information
# The motivation is that summing the information of the rows collapses
# the information in a way that is most likely not meaningful, and could
# therefore frustrate effective learning. Keeping all information is safer
# and only requires the linear layer of NetworkToSoftMaxNetwork to be sized
# such that it can process the larger input
class KeepAllActivationsResizer(ActivationsResizer):

    def __init__(self, network, data_height: int):
        super(KeepAllActivationsResizer, self).__init__(network)
        self.data_height = data_height

    def create_resized_activations(self, activations):
        activations_height = activations.size(2)

        # print("create_resized_activations - activations.size: " + str(activations.size()))

        # It can be that the activations dimensionality is not exactly 1, in which case it is reduced to 1
        # by simple summation
        if activations_height != 1:
            # raise RuntimeError("Error: the height dimension of returned activations should be of size 1, but it " +
            #                   "was: " + str(activations.size(2)))
            # print("WARNING: activations height is " + str(activations_height) + " ( > 1) :\n" +
            #      "Converting to a height of 1 by summing over the rows")
            activation_rows_list = list([])
            for i in range(0, activations.size(2)):
                activation_row = activations[:, :, i, :]
                activation_rows_list.append(activation_row)
            # Concatenate the activation rows on the channel dimension
            result = torch.cat(activation_rows_list, dim=1)
        else:
            result = activations
        # print("create_resized_activations - result.size: " +str(result.size()))
        return result

    @abstractmethod
    def get_number_of_output_channels(self):
        print("KeepAllActivationsResizer.get_number_of_output_channels  - data_height: " + str(self.data_height))
        print("KeepAllActivationsResizer.get_number_of_output_channels  - network.height_reduction_factor: " +
              str(self.network.get_height_reduction_factor()))
        number_of_rows_generated_by_network = int(self.data_height / self.network.get_height_reduction_factor())
        print("Number of rows generated by network: " + str(number_of_rows_generated_by_network))
        print("KeepAllActivationsResizer.get_number_of_output_channels  - network.get_number_of_output_channels(): " +
              str(self.network.get_number_of_output_channels()))
        return number_of_rows_generated_by_network * self.network.get_number_of_output_channels()


# This network takes a network as input and adds a linear layer
# that maps the input network's output to a sequential output
# of dimension: batch_size * number_of_output_channels * number_of_classes
class NetworkToSoftMaxNetwork(torch.nn.Module):
    def __init__(self, network, input_size: SizeTwoDimensional,
                 number_of_classes_excluding_blank: int,
                 activations_resizer: ActivationsResizer,
                 clamp_gradients: bool
                 ):
        super(NetworkToSoftMaxNetwork, self).__init__()
        self.clamp_gradients = clamp_gradients
        self.network = network
        self.activations_resizer = activations_resizer
        self.input_size = input_size
        self.number_of_output_channels = activations_resizer.get_number_of_output_channels()
        self.number_of_classes_excluding_blank = number_of_classes_excluding_blank

        print(">>> number_of_output_channels: " + str(self.number_of_output_channels))

        self.fc3 = nn.Linear(self.number_of_output_channels, self.get_number_of_classes_including_blank())



        # It is not totally clear actually whether "xavier_normal" or "xavier_uniform" initialization
        # is to be preferred
        # https://datascience.stackexchange.com/questions/13061/
        # when-to-use-he-or-glorot-normal-initialization-over-uniform-init-and-what-are
        #
        # However, the paper "Handwriting Recognition with Large Multidimensional
        # Long Short Term Memory Recurrent Neural Networks" gives better results
        # with using Xavier Glorot uniform initialization throughout, so we
        # go with that as well
        #  See: https://ieeexplore.ieee.org/document/7814068/

        # Initialize the linear output layer with Xavier uniform  weights
        # torch.nn.init.xavier_normal_(self.fc3.weight)
        torch.nn.init.xavier_uniform_(self.fc3.weight)
        # print("self.fc3 : " + str(self.fc3))
        # print("self.fc3.weight: " + str(self.fc3.weight))
        # print("self.fc3.bias: " + str(self.fc3.bias))

        print("NetworkToSoftMaxNetwork - clamp_gradients: " + str(clamp_gradients))


    @staticmethod
    def create_network_to_soft_max_network(network, input_size: SizeTwoDimensional,
                                           number_of_classes_excluding_blank: int,
                                           data_height: int, clamp_gradients: bool):
        activations_resizer = KeepAllActivationsResizer(network, data_height)
        # activations_resizer = SumActivationsResizer(network)
        return NetworkToSoftMaxNetwork(network, input_size, number_of_classes_excluding_blank,
                                       activations_resizer,
                                       clamp_gradients
                                       )

    def get_number_of_classes_including_blank(self):
        return self.number_of_classes_excluding_blank + 1

    def set_training(self, training):
        self.network.set_training(training)

    def forward(self, x):
        activations = self.network(x)
        batch_size = activations.size(0)
        # print(">>> activations.size(): " + str(activations.size()))
        # print("activations: " + str(activations))

        activations_height = activations.size(2)

        activations = self.activations_resizer.create_resized_activations(activations)

        activations_height_removed = activations.squeeze(2)
        activations_with_swapped_channels_and_width = activations_height_removed.transpose(1, 2)
        # print(">>> activations_with_swapped_channels_and_width.size(): " +
        #      str(activations_with_swapped_channels_and_width.size()))
        activations_resized_one_dimensional = activations_with_swapped_channels_and_width.contiguous().\
            view(-1, self.number_of_output_channels)

        # print("activations_resized_one_dimensional: " + str(activations_resized_one_dimensional))
        class_activations = self.fc3(activations_resized_one_dimensional)

        if self.clamp_gradients:
            # print("NetworkToSoftMaxNetwork - register gradient clamping...")
            class_activations = InsideModelGradientClamping.register_gradient_clamping(class_activations)


        # print("class_activations: " + str(class_activations))
        class_activations_resized = class_activations.view(batch_size, -1,
                                                             self.get_number_of_classes_including_blank())
        # print("class_activations.size(): " + str(class_activations.size()))

        # print("class_activation_resized: " + str(class_activations_resized))
        # print(">>> class_activations_resized.size(): " +
        #      str(class_activations_resized.size()))

        # print(">>> MultiDimensionalRNNToSoftMaxNetwork.forward.class activations: " + str(class_activations))
        # The dimension along which softmax must make probabilities to sum to one is the classes dimension
        probabilities_sum_to_one_dimension = 2
        # result = torch.nn.functional.log_softmax(class_activations_resized, probabilities_sum_to_one_dimension)
        # result = torch.nn.functional.softmax(class_activations_resized, probabilities_sum_to_one_dimension)

        # https://github.com/SeanNaren/deepspeech.pytorch/issues/136
        # "SeanNaren:
        # warp-ctc does the softmax in the function,
        # which is why we have this inference based softmax added to the network!"
        # Don't compute softmax
        result = class_activations_resized

        # print(">>> MultiDimensionalRNNToSoftMaxNetwork.forward.result: " + str(result))

        return result

    # Get the factor by which the original input width is reduced in the output
    # of the network
    def get_width_reduction_factor(self):
        return self.network.get_width_reduction_factor()
