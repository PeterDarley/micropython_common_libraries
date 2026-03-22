""" Package to hold utility functions """

def bytes_to_int(first_byte, second_byte):
        """ Convert two bytes to an integer. """

        if not first_byte & 0x80:
            return first_byte << 8 | second_byte
        return - (((first_byte ^ 255) << 8) | (second_byte ^ 255) + 1)