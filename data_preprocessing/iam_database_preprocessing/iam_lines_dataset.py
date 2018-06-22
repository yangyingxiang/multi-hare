from torch.utils.data import Dataset, DataLoader
from data_preprocessing.iam_database_preprocessing.iam_lines_dictionary import IamLinesDictionary
from data_preprocessing.iam_database_preprocessing.iam_lines_dictionary import IamLineInformation
from data_preprocessing.iam_database_preprocessing.string_to_index_mapping_table import StringToIndexMappingTable
from skimage import io, transform
import torch
import numpy
import os
import sys
import util.image_visualization


class IamLinesDataset(Dataset):
    EXAMPLE_TYPES_OK = "ok"
    EXAMPLE_TYPES_ERROR = "error"
    EXAMPLE_TYPES_ALL = "all"
    UINT8_WHITE_VALUE = 255


    def __init__(self, iam_lines_dictionary: IamLinesDictionary,
                 examples_line_information: list,
                 string_to_index_mapping_table: StringToIndexMappingTable,
                 height_required_per_network_output_row: int,
                 width_required_per_network_output_column: int,
                 transform=None):
        self.iam_lines_dictionary = iam_lines_dictionary
        self.examples_line_information = examples_line_information
        self.string_to_index_mapping_table = string_to_index_mapping_table
        self.transform = transform
        self.height_required_per_network_output_row = height_required_per_network_output_row
        self.width_required_per_network_output_column = width_required_per_network_output_column

    @staticmethod
    def get_examples_line_information(iam_lines_dictionary: IamLinesDictionary, example_types: str):
        if example_types == IamLinesDataset.EXAMPLE_TYPES_OK:
            examples_key_value_enumeration = iam_lines_dictionary.get_ok_examples()
        elif example_types == IamLinesDataset.EXAMPLE_TYPES_ERROR:
            examples_key_value_enumeration = iam_lines_dictionary.get_error_examples()
        elif example_types == IamLinesDataset.EXAMPLE_TYPES_ALL:
            examples_key_value_enumeration = iam_lines_dictionary.get_all_examples()
        else:
            raise RuntimeError("Error: IamLinesDataSet.get_examples(): unrecognized examples type")

        result = list([])
        for key, value in examples_key_value_enumeration:
            result.append(value)

        return result

    @staticmethod
    def create_string_to_index_mapping_table(examples_line_information):
        string_to_index_mapping_table = StringToIndexMappingTable.create_string_to_index_mapping_table()
        # print("examples_line_information: \n" + str(examples_line_information))
        for iam_line_information in examples_line_information:
            letters = iam_line_information.get_characters()
            string_to_index_mapping_table.add_strings(letters)
        return string_to_index_mapping_table

    @staticmethod
    def create_iam_lines_dataset(iam_lines_dictionary: IamLinesDictionary,
                                 example_types: str = EXAMPLE_TYPES_OK, transformation=None):
        examples_line_information = IamLinesDataset.get_examples_line_information(iam_lines_dictionary, example_types)
        string_to_index_mapping_table = IamLinesDataset.create_string_to_index_mapping_table(examples_line_information)

        # TODO : compute these from an input parameter
        height_required_per_network_output_row = 64
        width_required_per_network_output_column = 8

        print("string_to_index_mapping_table: " + str(string_to_index_mapping_table))
        return IamLinesDataset(iam_lines_dictionary, examples_line_information, string_to_index_mapping_table,
                               height_required_per_network_output_row, width_required_per_network_output_column,
                               transformation)

    def split_random_train_set_and_test_set(self, test_examples_fraction):
        random_permutation = numpy.random.permutation(self.__len__())
        last_train_index = int(self.__len__() * (1 - test_examples_fraction) - 1)
        # print("last_train_index: " + str(last_train_index))
        examples_line_information_train = \
            [self.examples_line_information[i] for i in random_permutation[0:last_train_index]]
        examples_line_information_test = \
            [self.examples_line_information[i] for i in random_permutation[last_train_index:]]
        train_set = IamLinesDataset(self.iam_lines_dictionary, examples_line_information_train,
                        self.string_to_index_mapping_table, 64, 8, self.transform)
        test_set = IamLinesDataset(self.iam_lines_dictionary, examples_line_information_test,
                                    self.string_to_index_mapping_table, 64, 8, self.transform)

        return train_set, test_set

    def get_vocabulary_list(self):
        return self.string_to_index_mapping_table.get_vocabulary_list()

    def get_blank_symbol(self):
        return self.string_to_index_mapping_table.get_blank_symbol()

    @staticmethod
    def get_labels_with_probabilities_length_and_real_sequence_length(labels_padded,
                                                                      original_image_width: int,
                                                                      labels_sequence_length: int):
        result = labels_padded.clone()
        result = torch.cat((result, torch.IntTensor([-original_image_width])), 0)
        result = torch.cat((result, torch.IntTensor([-labels_sequence_length])), 0)
        return result

    # This method converts a tensor with uint8 values in the range 0-255 to
    # a float tensor with values in the range 0-1
    @staticmethod
    def convert_unsigned_int_image_tensor_to_float_image_tensor(image_tensor):
        result = image_tensor.type(torch.FloatTensor)
        result = torch.div(result, 255)
        return result

    @staticmethod
    def compute_max_adjusted_by_scale_reduction_factor(max_value, scale_reduction_factor):
        return int((max_value + (max_value % scale_reduction_factor)) / scale_reduction_factor)

    def get_data_loader_with_appropriate_padding(self, data_set, max_image_height,
                                                 max_image_width, max_labels_length,
                                                 batch_size: int):
        to_tensor = ToTensor()

        train_set_pairs = list([])

        # Rescale the image
        scale_reduction_factor = 2

        # Not only must images be padded to be all the same size, they
        # must also be of a size that is a multiple of block_size squared
        # FIXME: This is a quick and dirty hack to get the sizes right
        # Implement for general block sizes

        max_image_height = IamLinesDataset.compute_max_adjusted_by_scale_reduction_factor(max_image_height,
                                                                                          scale_reduction_factor)
        max_image_width = IamLinesDataset.compute_max_adjusted_by_scale_reduction_factor(max_image_width,
                                                                                         scale_reduction_factor)
        max_image_height = max_image_height + (self.height_required_per_network_output_row
                                               - (max_image_height % self.height_required_per_network_output_row))
        # Width that is strictly required to fit the max occurring width and also
        # be a multiple of self.width_required_per_network_output_row
        max_image_width = max_image_width + (self.width_required_per_network_output_column
                                             - (max_image_width % self.width_required_per_network_output_column))

        sample_index = 0
        last_percentage_complete = 0

        for sample in data_set:
            image_original = sample["image"]
            image_original_dtype = image_original.dtype

            # print("image_original: " + str(image_original))
            # print("image_original.dtype: " + str(image_original.dtype))

            image_height_original, image_width_original = image_original.shape

            image_height = int(image_height_original / scale_reduction_factor)
            image_width = int(image_width_original / scale_reduction_factor)
            rescale = Rescale(tuple([image_height, image_width]))

            # Rescale works on ndarrays, not on pytorch tensors
            # print("before: sample[\"image\"].dtype: " + str(sample["image"].dtype))
            sample = rescale(sample)
            image_rescaled = sample["image"]
            # print("image_rescaled.dtype: " + str(image_rescaled.dtype))

            # Sanity check that the image dtype remained the same
            if image_original_dtype != image_rescaled.dtype:
                raise RuntimeError("Error: the image dtype changed")

            sample_pytorch = to_tensor(sample)

           # print("sample_pytorch: " + str(sample_pytorch))

            image = sample_pytorch["image"]
            # print(">>> image size: " + str(image.size()))

            # Get the labels and labels length
            labels = sample_pytorch["labels"]
            labels_length = labels.size(0)




            # print("image_height: " + str(image_height))
            # print("image_width: " + str(image_width))
            # print("labels_length: " + str(labels_length))

            columns_padding_required = max_image_width - image_width
            rows_padding_required = max_image_height - image_height
            rows_padding_required_top = int(rows_padding_required / 2)
            # Make sure no row gets lost through integer division
            rows_padding_required_bottom = rows_padding_required - rows_padding_required_top

            # print("columns_padding_required: " + str(columns_padding_required))
            # print("rows_padding_required: " + str(rows_padding_required))

            # See: https://pytorch.org/docs/stable/_modules/torch/nn/functional.html
            # pad last dimension (width) by 0, columns_padding_required
            # and one-but-last dimension (height) by 0, rows_padding_required
            # p2d = (0, columns_padding_required, rows_padding_required, 0)
            p2d = (0, columns_padding_required,
                   rows_padding_required_top,
                   rows_padding_required_bottom)
            # We want to pad with the value for white, which is 255 for uint8
            image_padded = torch.nn.functional. \
                pad(image, p2d, "constant", IamLinesDataset.UINT8_WHITE_VALUE)

            # Show padded image: for debugging
            # print("image: " + str(image))
            # util.image_visualization.imshow_tensor_2d(image_padded)

            # Add additional bogus channel dimension, since a channel dimension is expected by downstream
            # users of this method
            # print("before: image_padded.size(): " + str(image_padded.size()))
            image_padded = image_padded.unsqueeze(0)
            # print("after padding: image_padded.size(): " + str(image_padded.size()))
            # print("after padding: image_padded: " + str(image_padded))

            # image_padded = IamLinesDataset.convert_unsigned_int_image_tensor_to_float_image_tensor(image_padded)
            # print("after padding and type conversion: image_padded: " + str(image_padded))

            digits_padding_required = max_labels_length - labels_length
            p1d = (0, digits_padding_required)  # pad last dim by 1 on end side
            labels_padded = torch.nn.functional. \
                pad(labels, p1d, "constant", -2)
            labels_padded = IamLinesDataset.\
                get_labels_with_probabilities_length_and_real_sequence_length(labels_padded, image_width,
                                                                              labels_length)

            train_set_pairs.append(tuple((image_padded, labels_padded)))

            percentage_complete = int((float(sample_index) / len(data_set)) * 100)

            # Print every 10% if not already printed
            if ((percentage_complete % 10) == 0) and (percentage_complete != last_percentage_complete):
                print("iam_lines_dataset.get_data_loader_with_appropriate_padding - completed " +
                      str(percentage_complete) + "%")
                # print(" sample index: " + str(sample_index) + " len(data_set):" +
                #      str(len(data_set)))
                last_percentage_complete = percentage_complete
            sample_index += 1

        train_loader = torch.utils.data.DataLoader(
            dataset=train_set_pairs,
            batch_size=batch_size,
            shuffle=True)
        return train_loader

    def get_random_train_set_test_set_data_loaders(self, batch_size: int,
                                                   test_examples_fraction: float):

        max_image_height, max_image_width = self.get_max_image_dimension()
        # print("max image height: " + str(max_image_height))
        # print("max image width: " + str(max_image_width))
        max_labels_length = self.get_max_labels_length()
        # print("max labels length: " + str(max_labels_length))

        train_set, test_set = self.split_random_train_set_and_test_set(test_examples_fraction)

        print("Prepare IAM data train loader...")
        train_loader = self.get_data_loader_with_appropriate_padding(train_set, max_image_height, max_image_width,
                                                                     max_labels_length, batch_size)

        print("Prepare IAM data test loader...")
        test_loader = self.get_data_loader_with_appropriate_padding(test_set, max_image_height, max_image_width,
                                                                    max_labels_length, batch_size)

        return train_loader, test_loader

    def __len__(self):
        # return len(self.examples_line_information)
        return int(len(self.examples_line_information) / 20)  # Hack for faster training during development

    def __getitem__(self, idx):
        line_information = self.examples_line_information[idx]
        # print("__getitem__ line_information: " + str(line_information))
        image_file_path = self.iam_lines_dictionary.get_image_file_path(line_information)
        # print("image_file_path: " + str(image_file_path))

        sample = {'image': self.get_image(idx), 'labels': self.get_labels(idx)}

        if self.transform:
            sample = self.transform(sample)

        return sample

    def get_image(self, index):
        line_information = self.examples_line_information[index]
        # print("__getitem__ line_information: " + str(line_information))
        image_file_path = self.iam_lines_dictionary.get_image_file_path(line_information)
        # print("image_file_path: " + str(image_file_path))

        image = io.imread(image_file_path)
        # print(">>> get_image - image.dtype: " + str(image.dtype))
        return image

    def get_labels(self, index):
        line_information = self.examples_line_information[index]
        # print("__getitem__ line_information: " + str(line_information))
        image_file_path = self.iam_lines_dictionary.get_image_file_path(line_information)
        # print("image_file_path: " + str(image_file_path))

        characters = line_information.get_characters()
        indices = self.string_to_index_mapping_table.get_indices(characters)

        indices_array = numpy.ndarray((len(indices)), buffer=numpy.array(indices), dtype=int)

        return indices_array

    # Print statistics about the size of the images in the dataset. This useful to get an idea about how much
    # computation we "waste" by padding everything to the maximum width and maximum height
    @staticmethod
    def print_image_dimension_statistics(min_height, max_height, mean_height, min_width, max_width, mean_width):
        print(">>> IamLinesDataset.get_max_image_dimensions: ")
        print(" min_height: " + str(min_height) + " max_height: " + str(max_height) +
              " mean_height: " + str(mean_height))
        print(" min_width: " + str(min_width) + " max_width: " + str(max_width) +
              " mean_width: " + str(mean_width))

    def get_max_image_dimension(self):
        # Initialize the minimum to the maximum integer value
        min_height = sys.maxsize
        max_height = 0
        summed_heights = 0

        # Initialize the minimum to the maximum integer value
        min_width = sys.maxsize
        max_width = 0
        summed_widths = 0

        for index in range(0, self.__len__()):
            image = self.get_image(index)
            height, width = image.shape

            min_height = min(min_height, height)
            min_width = min(min_width, width)

            max_height = max(max_height, height)
            max_width = max(max_width, width)

            summed_heights += height
            summed_widths += width

        mean_height = summed_heights / float(self.__len__())
        mean_width = summed_widths / float(self.__len__())

        IamLinesDataset.print_image_dimension_statistics(min_height, max_height, mean_height,
                                                         min_width, max_width, mean_width)

        return max_height, max_width

    def get_max_labels_length(self):
        max_length = 0

        for index in range(0, self.__len__()):
            labels = self.get_labels(index)
            # labels has only one element, but it is still a tuple
            # hence this notation is needed
            length,  = labels.shape
            # print("length: " + str(length))
            max_length = max(max_length, length)

        return max_length


class Rescale(object):
    """Rescale the image in a sample to a given size.

    Args:
        output_size (tuple or int): Desired output size. If tuple, output is
            matched to output_size. If int, smaller of image edges is matched
            to output_size keeping aspect ratio the same.
    """

    def __init__(self, output_size):
        assert isinstance(output_size, (int, tuple))
        self.output_size = output_size

    def __call__(self, sample):
        image, labels = sample['image'], sample['labels']

        image_type_original = image.dtype

        h, w = image.shape[:2]
        if isinstance(self.output_size, int):
            if h > w:
                new_h, new_w = self.output_size * h / w, self.output_size
            else:
                new_h, new_w = self.output_size, self.output_size * w / h
        else:
            new_h, new_w = self.output_size

        new_h, new_w = int(new_h), int(new_w)

        # img = transform.resize(image, (new_h, new_w))

        # print("rescale - image.dtype: " + str(image.dtype))

        # The option "preserve_range=True is crucial for preserving
        # ints, and not converting to floats
        # See: http://scikit-image.org/docs/dev/api/skimage.transform.html#skimage.transform.resize
        image_result = transform.resize(image, (new_h, new_w),
                               mode="constant", anti_aliasing=True,
                               preserve_range=True)

        # print("rescale - img_result.dtype: " + str(image_result.dtype))

        # Still need to convert back to the original type, since
        # transform has no option to preserve type. "unsafe" option is
        # needed to allow casting floats to ints
        # See: https://docs.scipy.org/doc/numpy-1.14.0/reference/generated/numpy.ndarray.astype.html
        image_result_converted_back = image_result.\
            astype(image_type_original, casting="unsafe")

        return {'image': image_result_converted_back, 'labels': labels}


class ToTensor(object):
    """Convert ndarrays in sample to Tensors."""

    def __call__(self, sample):
        image, labels = sample['image'], sample['labels']

        # swap color axis because
        # numpy image: H x W x C
        # torch image: C X H X W
        # image = image.transpose((2, 0, 1))

        # print("ToTensor.call - image.dtype: " + str(image.dtype))
        # Appearantly the right torch tensor type is determined automatically
        # https://github.com/pytorch/pytorch/issues/541
        torch_image = torch.from_numpy(image)
        # print("torch_image.size(): " + str(torch_image.size()))
        # FIXME: labels must be an np.ndarray
        return {'image': torch_image,
                'labels': torch.from_numpy(labels).type(torch.IntTensor)}


def test_iam_lines_dataset():
    lines_file_path = "/datastore/data/iam-database/ascii/lines.txt"
    iam_database_line_images_root_folder_path = "/datastore/data/iam-database/lines"
    iam_lines_dicionary = IamLinesDictionary.create_iam_dictionary(lines_file_path,
                                                                   iam_database_line_images_root_folder_path)
    iam_lines_dataset = IamLinesDataset.create_iam_lines_dataset(iam_lines_dicionary, "ok", None)
    sample = iam_lines_dataset[0]
    print("iam_lines_dataset[0]: " + str(sample))
    print("(iam_lines_dataset[0])[image]: " + str(sample["image"]))
    to_tensor = ToTensor()
    sample_image = sample["image"]
    print("sample_image: " + str(sample_image))
    torch_tensors = to_tensor(sample)
    print("torch_tensor['image'].size(): " + str(torch_tensors['image'].size()))
    print("torch_tensor['labels'].size(): " + str(torch_tensors['labels'].size()))

    print("torch_tensor['image']: " + str(torch_tensors['image']))
    print("torch_tensor['labels']: " + str(torch_tensors['labels']))

    # for i in range(0, torch_tensors['image'].view(-1).size(0)):
    #    print("torch_tensor['image'][" + str(i) + "]: " + str(torch_tensors['image'].view(-1)[i]))

    iam_lines_dataset.get_random_train_set_test_set_data_loaders(16, 0.5)


def test_iam_words_dataset():
    words_file_path = "/datastore/data/iam-database/ascii/words.txt"
    iam_database_word_images_root_folder_path = "/datastore/data/iam-database/words"
    iam_words_dicionary = IamLinesDictionary.create_iam_dictionary(words_file_path,
                                                                   iam_database_word_images_root_folder_path)
    iam_lines_dataset = IamLinesDataset.create_iam_lines_dataset(iam_words_dicionary, "ok", None)
    sample = iam_lines_dataset[0]
    print("iam_words_dataset[0]: " + str(sample))
    print("(iam_words_dataset[0])[image]: " + str(sample["image"]))
    to_tensor = ToTensor()
    sample_image = sample["image"]
    print("sample_image: " + str(sample_image))
    torch_tensors = to_tensor(sample)
    print("torch_tensor['image'].size(): " + str(torch_tensors['image'].size()))
    print("torch_tensor['labels'].size(): " + str(torch_tensors['labels'].size()))

    print("torch_tensor['image']: " + str(torch_tensors['image']))
    print("torch_tensor['labels']: " + str(torch_tensors['labels']))

    # for i in range(0, torch_tensors['image'].view(-1).size(0)):
    #    print("torch_tensor['image'][" + str(i) + "]: " + str(torch_tensors['image'].view(-1)[i]))


def main():
    test_iam_lines_dataset()
    # test_iam_words_dataset()


if __name__ == "__main__":
    main()
