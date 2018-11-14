import torch
import torch.nn as nn
from modules.size_two_dimensional import SizeTwoDimensional
from abc import abstractmethod
from modules.inside_model_gradient_clipping import InsideModelGradientClamping
from util.tensor_list_chunking import TensorListChunking
from util.tensor_utils import TensorUtils
import util.image_visualization
from data_preprocessing.last_minute_padding import LastMinutePadding
from modules.module_io_structuring import ModuleIOStructuring
from modules.mdlstm_examples_packing import MDLSTMExamplesPacking
import custom_data_parallel.data_parallel
from modules.fully_connected_layers import FullyConnectedLayers
from modules.fully_connected_layers_sharing_weights import FullyConnectedLayersSharingWeights


class ActivationsResizer:

    def __init__(self, network):
        self.network = network

    @abstractmethod
    def create_resized_activations(self, activations):
        raise RuntimeError("not implemented")

    @abstractmethod
    def get_number_of_output_channels(self):
        raise RuntimeError("not implemented")

    def get_network(self):
        return self.network


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

    def get_number_of_output_channels(self):


        print("KeepAllActivationsResizer.get_number_of_output_channels  - data_height: " + str(self.data_height))
        print("KeepAllActivationsResizer.get_number_of_output_channels  - network.height_reduction_factor: " +
              str(self.get_network().get_height_reduction_factor()))
        number_of_rows_generated_by_network = int(self.data_height / self.get_network().get_height_reduction_factor())
        print("Number of rows generated by network: " + str(number_of_rows_generated_by_network))
        print("KeepAllActivationsResizer.get_number_of_output_channels  - network.get_number_of_output_channels(): " +
              str(self.get_network().get_number_of_output_channels()))
        return number_of_rows_generated_by_network * self.get_network().get_number_of_output_channels()


# This network takes a network as input and adds a linear layer
# that maps the input network's output to a sequential output
# of dimension: batch_size * number_of_output_channels * number_of_classes
class NetworkToSoftMaxNetwork(torch.nn.Module):

    LINEAR_LAYER_GRADIENT_CLAMPING_BOUND = 10

    def __init__(self, network,
                 number_of_classes_excluding_blank: int,
                 activations_resizer: ActivationsResizer,
                 clamp_gradients: bool,
                 input_is_list: bool,
                 use_examples_packing: bool,
                 input_network_produces_multiple_output_directions: bool,
                 share_weights_across_directions: bool,
                 use_block_mdlstm: bool,
                 ):
        super(NetworkToSoftMaxNetwork, self).__init__()
        self.clamp_gradients = clamp_gradients
        self.input_is_list = input_is_list
        self.use_example_packing = use_examples_packing
        self.use_block_mdlstm = use_block_mdlstm
        self.network = network
        self.activations_resizer = activations_resizer
        self.number_of_output_channels = self.get_real_network().get_number_of_output_channels()
            #activations_resizer.get_number_of_output_channels()
        self.number_of_classes_excluding_blank = number_of_classes_excluding_blank
        self.input_network_produces_multiple_output_directions = input_network_produces_multiple_output_directions
        self.share_weights_across_directions = share_weights_across_directions

        print(">>> number_of_output_channels: " + str(self.number_of_output_channels))

        print("NetworkToSoftMaxNetwork - number of classes: " + str(self.get_number_of_classes_including_blank()))
        # TODO: This should be replaced by a list "fully_connected_layers"
        # Or in fact, it is smarter to use a 1d convolution with
        # stride 1 to mimic the linear channel, but enable grouping to
        # parallelize the computation of the multiple fully connected
        # layers, using grouping

        # A class "FullyConnectedLayer" should be created that takes care
        # of the efficient
        if self.input_network_produces_multiple_output_directions:
            # Old elaborate implementation, which turns out to be not necessary
            # self.fully_connected_layers = FullyConnectedLayers.create_fully_connected_layers(
            #    self.number_of_output_channels, self.get_number_of_classes_including_blank(), 4)

            # Instead of having four separate fully connected layers whose output is then summed,
            # the same effect can be achieved more effectively by simply having a linear layer
            # with four times as many inputs, each input connected to each of the outputs by
            # a weighted connection. Not only is this computationally more efficient, it also
            # fixes the problem that having four fully connected layers, each with their own bias
            # weights, and then summing them is redundant. Only one set of bias weight is needed
            # for the four layers combined, and having redundant bias weight could make learning
            # harder.
            print(">>> Using the Bluche network structure with a final fully connected layer combining " +
                  "the outputs of the third MDLSTM layer for four directions...")

            if self.share_weights_across_directions:
                print(">>> Creating a network-to-softmax layer combining MDLSTM outputs for multiple "
                      "directions with shared weights across directions (weight sharing)")
                self.fully_connected_layer = FullyConnectedLayersSharingWeights.\
                    create_fully_connected_layers_sharing_weights(
                        self.number_of_output_channels, self.get_number_of_classes_including_blank(), 4)

            else:
                print(">>> Creating a network-to-softmax layer combining MDLSTM outputs for multiple "
                      "directions with unique weights for each direction (no weight sharing)")
                self.fully_connected_layer = nn.Linear(self.number_of_output_channels * 4,
                                                       self.get_number_of_classes_including_blank())

        else:
            self.fully_connected_layer = nn.Linear(self.number_of_output_channels,
                                                   self.get_number_of_classes_including_blank())

        # MDLSTMExamplesPacking for the to-be-processed batch of examples
        # When example-packing is used, this must be computed at the beginning of the
        # forward function
        self.mdlstmn_examples_packing = None

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

        if self.use_weight_sharing_across_directions():
            # torch.nn.init.xavier_uniform_(self.fully_connected_layer.one_dimensional_grouped_convolution.weight)
            torch.nn.init.xavier_uniform_(self.fully_connected_layer.linear_layer.weight)
        else:
            torch.nn.init.xavier_uniform_(self.fully_connected_layer.weight)

        # print("self.fc3 : " + str(self.fc3))
        # print("self.fc3.weight: " + str(self.fc3.weight))
        # print("self.fc3.bias: " + str(self.fc3.bias))

        print("NetworkToSoftMaxNetwork - clamp_gradients: " + str(clamp_gradients))

    @staticmethod
    def create_network_to_soft_max_network(network,
                                           number_of_classes_excluding_blank: int,
                                           data_height: int, clamp_gradients: bool,
                                           input_is_list: bool,
                                           use_examples_packing: bool,
                                           input_network_produces_multiple_output_directions: bool,
                                           share_weights_across_directions: bool,
                                           use_block_mdlstm: bool=False,
                                           ):
        activations_resizer = KeepAllActivationsResizer(network, data_height)
        # activations_resizer = SumActivationsResizer(network)
        return NetworkToSoftMaxNetwork(network, number_of_classes_excluding_blank,
                                       activations_resizer,
                                       clamp_gradients, input_is_list,
                                       use_examples_packing,
                                       input_network_produces_multiple_output_directions,
                                       share_weights_across_directions,
                                       use_block_mdlstm
                                       )

    def use_weight_sharing_across_directions(self):
        return self.input_network_produces_multiple_output_directions and \
               self.share_weights_across_directions

    def get_weight_fully_connected_layer(self):
        if self.use_weight_sharing_across_directions():
            return self.fully_connected_layer.get_weight()
        else:
            return self.fully_connected_layer.weight

    def get_number_of_classes_including_blank(self):
        return self.number_of_classes_excluding_blank + 1

    def set_training(self, training):
        self.get_real_network().set_training(training)

    @staticmethod
    def collect_examples_activation_heights(activations, input_network_produces_multiple_output_directions: bool):
        examples_activation_heights = list([])
        for example_activations in activations:
            if input_network_produces_multiple_output_directions:
                example_activations_height = example_activations.size(2)
            else:
                example_activations_height = example_activations.size(1)
            examples_activation_heights.append(example_activations_height)
        return examples_activation_heights

    @staticmethod
    def collect_examples_activation_widths(activations, input_network_produces_multiple_output_directions: bool):
        examples_activation_widths = list([])
        for example_activations in activations:
            # print(">>> example_activations.size(): " + str(example_activations.size()))
            if input_network_produces_multiple_output_directions:
                example_activations_width = example_activations.size(3)
            else:
                example_activations_width = example_activations.size(2)
            examples_activation_widths.append(example_activations_width)
        return examples_activation_widths

    """ 
        Extracts concatenated height one activations from block activations.
        The method takes as input a list of activations, one for each example.
        It loops over all the examples, and for each collects the activation rows.
        Finally all these activation rows are concatenated, so the final output 
        is in the form:  
        example_1_row_1_activations, , ..., example_1_row_m_activations, 
        ..., 
        example_n_row_1_activations, ..., example_n_row_m_activations
        
        The motivation for removing the height dimension like this, is that the 
        examples can have different heights, but these can be removed for the 
        sake of the linear layer activation and later restored, retrieving the 
        example-specific activation rows that need to be summed to get the final 
        activations for each example.
    """
    @staticmethod
    def extract_concatenated_height_one_activations_from_block_activations(
            activations_per_example_list,
            input_network_produces_multiple_output_directions: bool):

        # print("network_to_softmax_network - activations sizes after dechunking: ")
        # for element in activations_per_example_list:
        #     print(">>> activations list element size - " + str(element.size()))

        if not input_network_produces_multiple_output_directions:
            raise RuntimeError("input_network_produces_multiple_output_directions = "
                               + str(input_network_produces_multiple_output_directions))

        activations_height_one = list([])
        for example_activations in activations_per_example_list:

            if input_network_produces_multiple_output_directions:
                # In this case the example_activations tensor is four dimensional, with
                # the second and third dimension being the height and width respectively
                if not example_activations.size(0) == 1:
                    raise RuntimeError("Expected dimension zero to be of size 1")
                # Remove the first (bogus) dimension
                example_activations = example_activations.squeeze(0)

            if example_activations.size(1) > 1:
                # print("example_activations.size(): " + str(example_activations.size()))

                activation_rows = torch.split(example_activations, 1, dim=1)
                # Debugging check
                ModuleIOStructuring.check_activation_rows_are_not_equal(activation_rows)
                activations_height_one.extend(activation_rows)
            else:
                activations_height_one.append(example_activations)

        # for example_activations in activations_height_one:
        #     print("activations_height_one_element size: " + str(example_activations.size()))

        # Create tensor with all activations concatenated on width dimension
        activations_single_tensor = torch.cat(activations_height_one, 2)
        # print("activations_single_tensor.size(): " + str(activations_single_tensor.size()))
        return activations_single_tensor

    # de-chunk the chunked activations, yielding a list with for every element
    # the activations of one example
    @staticmethod
    def dechunk_activations(activations_chunked, tensor_list_chunking):
        return tensor_list_chunking. \
            dechunk_block_tensor_concatenated_along_batch_dimension_changed_block_size(activations_chunked,
                                                                                       SizeTwoDimensional(1, 1))

    @staticmethod
    def get_activations_single_tensor_and_activation_heights_and_widths(
            activations, input_network_produces_multiple_output_directions: bool):
        # From the de-chunked activations, collect the examples activation heights and widths
        # for later use when recovering the class activations belonging to each example
        # after computing all the class activations in one go from one long concatenated tensor
        examples_activation_heights = NetworkToSoftMaxNetwork.collect_examples_activation_heights(
            activations, input_network_produces_multiple_output_directions)
        examples_activation_widths = NetworkToSoftMaxNetwork.collect_examples_activation_widths(
            activations, input_network_produces_multiple_output_directions)

        # Concatenate the activations of the examples
        activations_single_tensor = NetworkToSoftMaxNetwork. \
            extract_concatenated_height_one_activations_from_block_activations(
                activations, input_network_produces_multiple_output_directions)

        return activations_single_tensor, examples_activation_heights, examples_activation_widths

    def compute_activations_with_block_mdlstm(self, x):
        # print("network_to_softmax_network - network input x sizes: " )
        # for element in x:
        #     print(">>> input list element size - " + str(element.size()))
        network_consumed_block_size = SizeTwoDimensional(self.get_real_network().get_height_reduction_factor(),
                                                         self.get_real_network().get_width_reduction_factor())
        # print("Network_consumed_block_size: " + str(network_consumed_block_size))

        # # Plot two row images for debugging
        # for element in x:
        #     if element.size(1) > 64:
        #         print("image to be plotted size: " + str(element.size()))
        #         element_without_channel_dimension = element.squeeze(0)
        #         util.image_visualization.imshow_tensor_2d(element_without_channel_dimension)

        tensor_list_chunking = TensorListChunking.create_tensor_list_chunking(x, network_consumed_block_size)

        # Chunk the input
        input_chunked = tensor_list_chunking. \
            chunk_tensor_list_into_blocks_concatenate_along_batch_dimension(x, False)

        # print("input_chunked.size(): " + str(input_chunked.size()))

        # Debugging: check that the de-chunked version recovers the original
        ModuleIOStructuring.\
            check_dechunking_chunked_tensor_list_recovers_original(tensor_list_chunking, x, input_chunked)

        # print("input_chunked :" + str(input_chunked))

        # Compute the activations on the chunked input
        activations_chunked = self.network(input_chunked)
        # print("network_to_softmax_network - activations_chunked.size(): " + str(activations_chunked.size()))

        # de-chunk the chunked activations
        activations = NetworkToSoftMaxNetwork.dechunk_activations(activations_chunked, tensor_list_chunking)

        return NetworkToSoftMaxNetwork.get_activations_single_tensor_and_activation_heights_and_widths(
            activations, self.input_network_produces_multiple_output_directions)

    @staticmethod
    def resize_activations_block_mdlstm_minimal_padding(activations):
        # In this case there is no batch dimension, activations has three dimensions:
        # the first dimension is the number of
        # channels and everything is concatenated on the width dimension (the third dimension)
        # Activations of the format e.g.:  activations.size(): torch.Size([2048, 1, 24])
        activations_height_removed = activations.squeeze(1)
        activations_with_swapped_channels_and_width = activations_height_removed.transpose(0, 1)
        return activations_with_swapped_channels_and_width

    def resize_activations_padded_batch(self, activations):
        # In this case there is a batch dimension: activations has four dimensions:
        # batch_size, channels, height, width
        # Activations of the format e.g.:  activations.size(): torch.Size([4, 2048, 1, 12])
        # activations_resized_no_height = ModuleIOStructuring. \
        #     extract_and_concatenate_nonpadding_parts_activations(x, activations,
        #                                                          self.get_width_reduction_factor())
        height_times_width = activations.size(2) * activations.size(3)
        activations_height_removed = activations.view(activations.size(0), activations.size(1),
                                                      height_times_width)

        activations_with_swapped_channels_and_width = activations_height_removed.transpose(1, 2)
        # Change view to remove the batch dimension
        activations_resized_no_batch_dimension = activations_with_swapped_channels_and_width.contiguous(). \
            view(-1, self.number_of_output_channels)
        return activations_resized_no_batch_dimension

    @staticmethod
    def get_max_input_width(list_of_input_tensors):
        max_input_width = 0
        for example in list_of_input_tensors:
            # print("example.size(): " + str(example.size()))
            max_input_width = max(max_input_width, example.size(2))
        return max_input_width

    def create_padded_chunks(self, chunks, expected_output_width):
        chunks_padded = list([])
        for chunk in chunks:
            columns_padding_required = expected_output_width - chunk.size(1)
            # print("chunk.size: " + str(chunk.size()))
            # print("columns_padding_required: " + str(columns_padding_required))
            p1d = (0, columns_padding_required)
            # Padding is done to the last dimension but we need to padd the one-but last dimension
            # so transpose, padd, then transpose back
            chunk_transposed = chunk.transpose(1, 2)
            chunk_transposed_padded = torch.nn.functional.pad(chunk_transposed, p1d, "constant", 0)

            # torch.nn.functional.pad has a gradient and therefore needs to be
            # clamped to avoid that it can cause exploding gradients
            if self.clamp_gradients:
                InsideModelGradientClamping.clamp_grad(chunk_transposed_padded,
                                                       NetworkToSoftMaxNetwork.LINEAR_LAYER_GRADIENT_CLAMPING_BOUND)

            chunk_padded = chunk_transposed_padded.transpose(1, 2)
            chunks_padded.append(chunk_padded)
        return chunks_padded

    def forward(self, x, max_input_width=None):

        if self.input_is_list:

            if not isinstance(x, list):
                raise RuntimeError("Error: was expecting input to forward function "
                                   + "to be a list")

            if self.use_block_mdlstm:
                activations, examples_activation_heights, examples_activation_widths = \
                    self.compute_activations_with_block_mdlstm(x)
                # print("examples_activation_heights: " + str(examples_activation_heights))
                # print("activations.size(): " + str(activations.size()))
                max_input_width = 0
                for example in x:
                    # print("example.size(): " + str(example.size()))
                    max_input_width = max(max_input_width, example.size(2))

            elif self.use_example_packing:
                # print("network_to_softmax_network - use_examples_packing")
                # Group elements by height for more efficient computation in layers of the
                # MDLSTM layer pair stacking network at places where tensor_chunking is used
                reordered_elements_list, original_indices = TensorListChunking.group_examples_by_height(x)
                activations_reordered = self.network(reordered_elements_list)
                # Retrieve the original order
                activations = TensorListChunking.retrieve_original_order(activations_reordered, original_indices)
                # activations = self.network(reordered_elements_list)
                activations, examples_activation_heights, examples_activation_widths = \
                    NetworkToSoftMaxNetwork.get_activations_single_tensor_and_activation_heights_and_widths(
                        activations, self.input_network_produces_multiple_output_directions)

            else:
                last_minute_padding = LastMinutePadding(self.get_height_reduction_factor(),
                                                        self.get_width_reduction_factor())
                padded_examples_tensor, max_input_width = last_minute_padding.pad_and_cat_list_of_examples(x)

                # for index in range(0, padded_examples_tensor.size(0)):
                #     # if element.size(1) > 64:
                #     element = padded_examples_tensor[index, :, :, :]
                #     print("image to be plotted size: " + str(element.size()))
                #     element_without_channel_dimension = element.squeeze(0)
                #     element_without_channel_dimension = element_without_channel_dimension.cpu()
                #     /util.image_visualization.imshow_tensor_2d(element_without_channel_dimension)

                # print("x[0].device: " + str(x[0].device))
                # print("padded_examples_tensor.size(): " + str(padded_examples_tensor.size()))
                # print("padded_examples_tensor.requires_grad:" + str(padded_examples_tensor.requires_grad))
                # raise RuntimeError("stopping")
                activations = self.network(padded_examples_tensor)
        else:
            activations = self.network(x)
            max_input_width = x.size(3)

        # The expected output width of the network. All outputs must be this width,
        # because the warp_ctc loss expects a single width (i.e. requires padding)
        expected_output_width = int(max_input_width / self.get_width_reduction_factor())

        batch_size = activations.size(0)
        number_of_activation_rows = activations.size(2)
        # print(">>> activations.size(): " + str(activations.size()))
        # print("activations: " + str(activations))

        # activations_height = activations.size(2)
        #
        # # Activations resizing should only be done in the old way with activations
        # # resizer if the input is not a list
        # # if not self.input_is_list:
        # #    activations = self.activations_resizer.create_resized_activations(activations)
        #
        # activations_height_removed = activations.squeeze(2)
        # activations_with_swapped_channels_and_width = activations_height_removed.transpose(1, 2)
        # # print(">>> activations_with_swapped_channels_and_width.size(): " +
        # #      str(activations_with_swapped_channels_and_width.size()))
        # activations_resized_no_height = activations_with_swapped_channels_and_width.contiguous().\
        #     view(-1, self.number_of_output_channels)

        # Restructure the activations to be 2-dimensional, with the first dimension
        # the number of activations and the second dimension the number of channels
        if (self.use_block_mdlstm or self.use_example_packing) and self.input_is_list:
            activations_resized_two_dimensional = \
                NetworkToSoftMaxNetwork.resize_activations_block_mdlstm_minimal_padding(activations)
        else:
            activations_resized_two_dimensional = self.resize_activations_padded_batch(activations)


        # print("activations_resized_no_height: " + str(activations_resized_no_height))
        class_activations = self.fully_connected_layer(activations_resized_two_dimensional)

        if self.clamp_gradients:
            # print("NetworkToSoftMaxNetwork - register gradient clamping...")
            class_activations = InsideModelGradientClamping.\
                register_gradient_clamping(class_activations,
                                           NetworkToSoftMaxNetwork.LINEAR_LAYER_GRADIENT_CLAMPING_BOUND,
                                           False, "network to softmax network - class_activations")

        # print("class_activations: " + str(class_activations))
        if self.input_is_list and (self.use_block_mdlstm or self.use_example_packing):
            # print("class_activations.size(): " + str(class_activations.size()))

            class_activations_resized_temp = class_activations.view(1, -1, self.get_number_of_classes_including_blank())
            # print("class_activations_resized_temp.size(): " + str(class_activations_resized_temp.size()))
            # print("examples_activation_widths: " + str(examples_activation_widths))

            chunks = ModuleIOStructuring.extract_activation_chunks(examples_activation_heights,
                                                                   examples_activation_widths,
                                                                   class_activations_resized_temp)
            # for chunk in chunks:
            #     print("chunk.size(): " + str(chunk.size()))

            chunks_padded = self.create_padded_chunks(chunks, expected_output_width)
            class_activations_resized = torch.cat(chunks_padded, 0)
        else:
            # print("class_activations.size(): " + str(class_activations.size()))
            class_activations = NetworkToSoftMaxNetwork.\
                get_class_activations_summed_over_height_from_2d_activations_tensor(class_activations,
                                                                                    number_of_activation_rows,
                                                                                    self.clamp_gradients)
            class_activations_resized = class_activations.view(batch_size, -1,
                                                               self.get_number_of_classes_including_blank())



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

        # Sanity check that the network produces an output width that matches the
        # (padded) input width
        # print("class_activations_resized.size(): " + str(class_activations_resized.size()))
        output_width = class_activations_resized.size(1)
        if not output_width == expected_output_width:
            raise RuntimeError("Error: Expected output_width: " + str(expected_output_width) +
                               " but got: " + str(output_width))

        # print(">>> MultiDimensionalRNNToSoftMaxNetwork.forward.result: " + str(result))
        # print(">>> MultiDimensionalRNNToSoftMaxNetwork.forward.result.size(): " + str(result.size()))
        return result

    # Gets the class activations summed over height, from a 2D tensor of class activations
    # in which all the class activation rows from the first example are concatenated, followed
    # by all rows for the second example etc
    # This method requires that all the concatenated rows are of equal length in the input
    # tensor, that is, the class activations have been computed from padded activation
    # tensors
    @staticmethod
    def get_class_activations_summed_over_height_from_2d_activations_tensor(class_activations,
                                                                            number_of_activation_rows,
                                                                            clamp_gradients):
        # print("get_class_activations_summed_over_height - number_of_activation_rows: "
        # + str(number_of_activation_rows))
        # print("get_class_activations_summed_over_height - input.size(): " + str(class_activations.size()))
        columns_after_height_extraction = int(class_activations.size(0) / number_of_activation_rows)
        class_activations_with_height = class_activations.view(columns_after_height_extraction,
                                                               number_of_activation_rows, -1)
        result = torch.sum(class_activations_with_height, 1)
        if clamp_gradients:
            InsideModelGradientClamping.\
                register_gradient_clamping(result,
                                           NetworkToSoftMaxNetwork.LINEAR_LAYER_GRADIENT_CLAMPING_BOUND,
                                           False,
                                           "network_to_softmax_network - result")
        # print("get_class_activations_summed_over_height - result.size(): " + str(result.size()))
        return result

    def get_real_network(self):
        return self.activations_resizer.get_network()

    # Get the factor by which the original input width is reduced in the output
    # of the network
    def get_width_reduction_factor(self):
        return self.get_real_network().get_width_reduction_factor()

    # Get the factor by which the original input height is reduced in the output
    # of the network
    def get_height_reduction_factor(self):
        return self.get_real_network().get_height_reduction_factor()

    def compute_mdlstm_examples_packing(self, examples):
        return MDLSTMExamplesPacking.created_mdlstm_examples_packing(examples, self.get_width_reduction_factor())




