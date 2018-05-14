import torch
from util.utils import Utils
from torch.autograd import Variable


class ImageInputTransformer:
    
    # This method takes an image and creates a transformed image, shifting the i-th row
    # with i pixels. This corresponds to the transformation used in the
    # pixel recurrent neural networks paper (https://arxiv.org/pdf/1601.06759.pdf)
    # This trick can be used to efficiently compute Multi-dimensional RNNs, while
    # keeping the input the same for every layer of the network
    #
    @staticmethod
    def create_row_diagonal_offset_tensor(image_tensor):
        #See: https://stackoverflow.com/questions/46826218/pytorch-how-to-get-the-shape-of-a-tensor-as-a-list-of-int

        #print("list(image_tensor.size()): " + str(list(image_tensor.size())))
        # See: https://discuss.pytorch.org/t/indexing-a-2d-tensor/1667/2
        height = image_tensor.size(1)
        width = image_tensor.size(2)

        #number_of_image_tensors  = image_tensor.size(0)
        #print("number of image tensors: " + str(number_of_image_tensors))
        transformed_image = torch.zeros(1, height, (width * 2) - 1)
        #print("transformed_image: " + str(transformed_image))
        # print("transformed_image.size(): " + str(transformed_image.size()))

        for row_number in range(image_tensor.size(1)):
            leading_zeros = row_number
            tailing_zeros = transformed_image.size(2) - width - row_number
            #print("leading zeros: " + str(leading_zeros))
            #print("tailing_zeros: " + str(tailing_zeros))
            #print(" image_tensor[0][y][:]) : " + str( image_tensor[0][y][:]))
            if leading_zeros > 0:
                new_row = torch.cat((torch.zeros(leading_zeros), image_tensor[0, row_number, :]), 0)
            else:
                new_row = image_tensor[0, row_number][:]
            #print("new row: " + str(new_row))
            new_row = torch.cat((new_row, torch.zeros(tailing_zeros)), 0)
            #print("new row: " + str(new_row))
            #print("transformed_image[0][y][:] : " + str(transformed_image[0][y][:]))
            transformed_image[0, row_number, :] = new_row[:]
            #for x in range(image_tensor.size(2)):
            #    # The transformed_image i'th row is shifted by i positions
            #    # print("image_tensor[0][x][y]: " + str(image_tensor[0][y][x]))
            #    # print("x: " + str(x) + " y: " + str(y))
            #    print("image_tensors: " + str(image_tensor))
            #    print("image_tensor[0][0][0]: " + str(image_tensor[0][0][0]))
            #    print("transformed_image[0][y][x + y]" + str( transformed_image[0][y][x + y]))
            #    transformed_image[0][y][x + y] = image_tensor[0][y][x]
        return transformed_image

    # Non-optimized method, that computes the skewed images one at a time, then
    # concatenates them in a for loop
    @staticmethod
    def create_row_diagonal_offset_tensors_serial(image_tensors):
        image_tensor = image_tensors[0, :, :, :]
        result = ImageInputTransformer.create_row_diagonal_offset_tensor(image_tensor)
        number_of_tensors = image_tensors.size(0)
        for i in range(1, number_of_tensors):
        # print("image number: " + str(i))
            skewed_image = ImageInputTransformer.create_row_diagonal_offset_tensor(image_tensors[i, :, :, :])
            result = torch.cat((result, skewed_image), 0)
        return result

    @staticmethod
    def get_skewed_images_width(original_image_tensors):
        height = original_image_tensors.size(2)
        width = original_image_tensors.size(3)
        transformed_images_width = height + width - 1
        return transformed_images_width


    # Optimized method computes the complete set of skewed images all in one go
    # using pytorch tensor indexing to select slices of rows from multiple images
    # at one, doing the operation for all images in parallel
    # Requirement: all images must be of the same size. This implementation seems
    # break the gradient, although this is not sure. In either case it is also slower
    # than the pytorch.cat based implementation
    @staticmethod
    def create_row_diagonal_offset_tensors_parallel_breaks_gradient(image_tensors):

        if Utils.use_cuda():
            # https://discuss.pytorch.org/t/which-device-is-model-tensor-stored-on/4908/7
            device = image_tensors.get_device()

        number_of_channels = image_tensors.size(1)
        height = image_tensors.size(2)
        width = image_tensors.size(3)

        number_of_image_tensors  = image_tensors.size(0)

        transformed_images = torch.zeros(number_of_image_tensors, number_of_channels, height,
                                         ImageInputTransformer.get_skewed_images_width(image_tensors))

        for y in range(image_tensors.size(2)):
            leading_zeros = y
            tailing_zeros = transformed_images.size(3) - width - y

            if leading_zeros > 0:

                # To get a sub-tensor with everything from the 0th and 3th dimension,
                # and specific values for the 1th  and 2nd dimension you use
                # image_tensors[:, 0, y, :]
                # See:
                # https://stackoverflow.com/questions/47374172/how-to-select-index-over-two-dimension-in-pytorch?rq=1
                leading_zeros_tensor = torch.zeros(number_of_image_tensors, number_of_channels,
                                                   leading_zeros)
                if Utils.use_cuda():
                    leading_zeros_tensor = leading_zeros_tensor.to(device)

                # print("leading_zeros_tensor.size()" + str(leading_zeros_tensor.size()))

                new_row = torch.cat((leading_zeros_tensor,
                                     image_tensors[:, :, y, :]), 2)
            else:
                new_row = image_tensors[:, :, y, :]

            if tailing_zeros > 0:
                # print("number of channels: " + str(number_of_channels))
                tailing_zeros_tensor = torch.zeros(number_of_image_tensors,
                                                   number_of_channels, tailing_zeros)
                if Utils.use_cuda():
                    tailing_zeros_tensor = tailing_zeros_tensor.to(device)

                # print("new_row.size(): " + str(new_row.size()))
                # print("tailing_zeros_tensor.size(): " + str(tailing_zeros_tensor.size()))
                new_row = torch.cat((new_row, tailing_zeros_tensor), 2)
            # print("new row.size(): " + str(new_row.size()))
            # print("transformed_image[:, :, y, :].size()" + str(transformed_images[:, :, y, :].size()))
            transformed_images[:, :, y, :] = new_row

        # This method creates CopySlices objects as gradients. Not clear if this is ok.
        # It may be harmless, but seems to be slower in any case
        # Something can be found about CopySlices at
        # https://github.com/pytorch/pytorch/blob/master/torch/csrc/autograd/functions/tensor.cpp
        # but this is not also very conclusive
        print("create_row_diagonal_offset_tensor_parallel_breaks_gradient: transformed_images.grad_fn: " +
              str(transformed_images.grad_fn))
        print("transformed_images.size(): " + str(transformed_images.size()))
        return transformed_images

    @staticmethod
    def create_transformed_images_row(row_number: int, number_of_image_tensors: int,
                                      number_of_channels: int,
                                      width: int, transformed_images_width,
                                      image_tensors, device):
        leading_zeros = row_number
        tailing_zeros = transformed_images_width - width - row_number

        if leading_zeros > 0:

            # To get a sub-tensor with everything from the 0th and 3th dimension,
            # and specific values for the 1th  and 2nd dimension you use
            # image_tensors[:, 0, y, :]
            # See:
            # https://stackoverflow.com/questions/47374172/how-to-select-index-over-two-dimension-in-pytorch?rq=1
            leading_zeros_tensor = torch.zeros(number_of_image_tensors, number_of_channels,
                                               leading_zeros)

            if Utils.use_cuda():
                leading_zeros_tensor = leading_zeros_tensor.to(device)

            # print("leading_zeros_tensor.size()" + str(leading_zeros_tensor.size()))

            new_row = torch.cat((leading_zeros_tensor,
                                 image_tensors[:, :, row_number, :]), 2)
        else:
            new_row = image_tensors[:, :, row_number, :]

        if tailing_zeros > 0:
            # print("number of channels: " + str(number_of_channels))
            tailing_zeros_tensor = torch.zeros(number_of_image_tensors,
                                               number_of_channels, tailing_zeros)
            if Utils.use_cuda():
                tailing_zeros_tensor = tailing_zeros_tensor.to(device)

            # print("new_row.size(): " + str(new_row.size()))
            # print("tailing_zeros_tensor.size(): " + str(tailing_zeros_tensor.size()))
            new_row = torch.cat((new_row, tailing_zeros_tensor), 2)
        return new_row

    # Optimized method computes the complete set of skewed images all in one go
    # using pytorch tensor indexing to select slices of rows from multiple images
    # at one, doing the operation for all images in parallel
    # Requirement: all images must be of the same size
    @staticmethod
    def create_row_diagonal_offset_tensors_parallel(image_tensors):

        if Utils.use_cuda():
            # https://discuss.pytorch.org/t/which-device-is-model-tensor-stored-on/4908/7
            device = image_tensors.get_device()

        # See: https://stackoverflow.com/questions/46826218/pytorch-how-to-get-the-shape-of-a-tensor-as-a-list-of-int

        # print("list(image_tensor.size()): " + str(list(image_tensors.size())))
        # See: https://discuss.pytorch.org/t/indexing-a-2d-tensor/1667/2
        number_of_channels = image_tensors.size(1)
        height = image_tensors.size(2)
        width = image_tensors.size(3)
        # print("height: " + str(height))
        # print("width: " + str(width))

        number_of_image_tensors = image_tensors.size(0)
        # print("number of image tensors: " + str(number_of_image_tensors))
        # The width of the transformed images is width+height-1 (important for unequal sized input_
        # transformed_images = torch.zeros(number_of_image_tensors, number_of_channels, height, (width + height) - 1)
        # print("transformed_image: " + str(transformed_image))
        # print("transformed_im   age.size(): " + str(transformed_image.size()))

        # The width of the transformed images is width+height-1 (important for unequal sized input_
        transformed_images_width = ImageInputTransformer.get_skewed_images_width(image_tensors)

        transformed_images = ImageInputTransformer. \
            create_transformed_images_row(0, number_of_image_tensors,
                                          number_of_channels,
                                          width, transformed_images_width, image_tensors, device)
        transformed_images = transformed_images.unsqueeze(2)
        # print("transformed_images.size(): " + str(transformed_images.size()))

        for row_number in range(1, height):
            new_row = ImageInputTransformer. \
                create_transformed_images_row(row_number, number_of_image_tensors,
                                              number_of_channels,
                                               width, transformed_images_width, image_tensors, device)
            new_row = new_row.unsqueeze(2)
            # print("new_row.size(): " + str(new_row.size()))
            # print("new row.size(): " + str(new_row.size()))
            # print("transformed_image[:, :, y, :].size()" + str(transformed_images[:, :, y, :].size()))
            #  transformed_images[:, :, y, :] = new_row

            # Use torch.cat instead of copying of a tensor slice into a zeros tensor.
            # torch.cat clearly preserves the backward gradient pointer, but with
            # copying to a zeros tensor it is not quite clear if this happens
            transformed_images = torch.cat((transformed_images, new_row), 2)

        # print("create_row_diagonal_offset_tensor: transformed_images.grad_fn: " + str(transformed_images.grad_fn))
        # print("transformed_images.size(): " + str(transformed_images.size()))
        return transformed_images

    @staticmethod
    def create_row_diagonal_offset_tensors(image_tensors):

        #result = ImageInputTransformer.create_row_diagonal_offset_tensors_serial(image_tensors[:, :, :, :])
        result = ImageInputTransformer.create_row_diagonal_offset_tensors_parallel(image_tensors[:, :, :, :])
        #print("result: " + str(result))
        return result

    @staticmethod
    def create_skewed_images_variable_four_dim(x):
        # skewed_images = ImageInputTransformer.create_row_diagonal_offset_tensors(x)

        ### Not clear if this method really causes the gradient to break or not.
        # skewed_images = ImageInputTransformer.\
        #    create_row_diagonal_offset_tensors_parallel_breaks_gradient(x)

        skewed_images = ImageInputTransformer. \
            create_row_diagonal_offset_tensors_parallel(x)

        # print("skewed images columns: " + str(skewed_images_columns))
        # print("skewed images rows: " + str(skewed_images_rows))
        # print("skewed_images: " + str(skewed_images))
        # See: https://pytorch.org/docs/stable/tensors.html

        if Utils.use_cuda():
            # https://discuss.pytorch.org/t/which-device-is-model-tensor-stored-on/4908/7
            device = x.get_device()
            skewed_images = skewed_images.to(device)
        return skewed_images

    @staticmethod
    def convert_activation_columns_list_to_tensor(activation_columns,
                                                  skewed_image_columns: int, ):

        # How to unskew the activation matrix, and retrieve an activation
        # matrix of the original image size?
        activations_column = activation_columns[0]
        # Columns will be horizontally concatenated, add extra dimension for this concatenation
        activations_column_unsqueezed = torch.unsqueeze(activations_column, 3)
        activations_as_tensor = activations_column_unsqueezed
        # print("activations_as_tensor.requires_grad: " + str(activations_as_tensor.requires_grad))
        for column_number in range(1, skewed_image_columns):
            # print("activations[column_number]: " + str(activations[column_number]))
            activations_column = activation_columns[column_number]
            # print("activations column: " + str(activations_column))
            activations_column_unsqueezed = torch.unsqueeze(activations_column, 3)
            activations_as_tensor = torch.cat((activations_as_tensor, activations_column_unsqueezed), 3)
        # print("activations_as_tensor.size(): " + str(activations_as_tensor.size()))

        return activations_as_tensor

    @staticmethod
    def extract_unskewed_activations_from_activation_tensor(activations_as_tensor,
                                                            original_image_columns: int,
                                                            skewed_image_rows: int):
        # print("original image columns: " + str(original_image_columns))

        # print("activations: " + str(activations))

        activations_unskewed = activations_as_tensor[:, :, 0, 0:original_image_columns]
        activations_unskewed = torch.unsqueeze(activations_unskewed, 2)
        # print("activations_unskewed before:" + str(activations_unskewed))
        for row_number in range(1, skewed_image_rows):
            # print("row_number: (original_image_columns + row_number: " +
            #      str(row_number) + ":" + str(original_image_columns + row_number))
            activation_columns = activations_as_tensor[:, :, row_number,
                                 row_number: (original_image_columns + row_number)]
            activation_columns = torch.unsqueeze(activation_columns, 2)
            # print("activations.size():" + str(activations.size()))
            # print("activations_unskewed.size():" + str(activations_unskewed.size()))
            activations_unskewed = torch.cat((activations_unskewed, activation_columns), 2)

        # activations_unskewed = MultiDimensionalRNNBase.break_activations_unskewed(activations_unskewed)

        return activations_unskewed

    # activation_columns is a list of activation columns
    @staticmethod
    def extract_unskewed_activations_from_activation_columns(activation_columns,
                                                             original_image_columns: int,
                                                             skewed_image_columns: int,
                                                             skewed_image_rows: int):

        activations_as_tensor = ImageInputTransformer. \
            convert_activation_columns_list_to_tensor(activation_columns, skewed_image_columns)
        return ImageInputTransformer. \
            extract_unskewed_activations_from_activation_tensor(activations_as_tensor,
                                                                original_image_columns,
                                                                skewed_image_rows)

    # Method that demonstrates and explains the bug of adding a superfluous variable
    # wrapping. What happens is that the additional wrapping makes
    # the variable into a leaf variable, with a non-existent (empty) gradient function
    # graph trace. This breaks the path used by back-propagation to
    # update previous upstream graph nodes, with catastrophic effect on the learning
    # results
    # See: https://pytorch.org/docs/0.2.0/_modules/torch/autograd/variable.html :
    # "
    # Variable is a thin wrapper around a Tensor object, that also holds
    # the gradient w.r.t. to it, and a reference to a function that created it.
    # This reference allows retracing the whole chain of operations that
    # created the data. If the Variable has been created by the user, its grad_fn
    # will be ``None`` and we call such objects *leaf* Variables.
    # "
    # So explicitly created Variables have an emtpy grad_fn field, in other words,
    # the gradient backwards path is lost, and hence updating predecessor variables
    # is made impossible, causing learning to fail.
    #
    @staticmethod
    def break_non_leaf_variable_by_wrapping_with_additional_variable(activations_unskewed):
        # If activations_unskewed is made a variable (again!) it still works but runs
        # much faster, but results are much worse somehow!!!
        # print("activations_unskewed before: " + str(activations_unskewed.grad))
        # print("activation_unskewed.requires_grad: " + str(activations_unskewed.requires_grad))
        # See: https://pytorch.org/docs/0.3.1/autograd.html
        # Wrapping into an additional variable makes activations_unskewed into a graph
        # leaf, which it isn't before the extra wrapping (what exactly does this mean?)
        print("before: activations_unskewed.is_leaf: " + str(activations_unskewed.is_leaf))
        print("before: activations_unskewed. grad_fn: " + str(activations_unskewed.grad_fn))
        activations_unskewed = Variable(activations_unskewed)
        print("after: activations_unskewed.is_leaf: " + str(activations_unskewed.is_leaf))
        print("after: activations_unskewed. grad_fn: " + str(activations_unskewed.grad_fn))
        # print("activations_unskewed after: " + str(activations_unskewed.grad))
        return activations_unskewed

